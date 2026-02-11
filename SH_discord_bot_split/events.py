# events.py
import asyncio
import time
import re
import discord

from app import client, tree, _last_prompt_time
from config import (
    TICKETS_CATEGORY_ID,
    PROMPT_COOLDOWN_SECONDS,
    WELCOME_MESSAGE,
    IGNORE_ADD_ADMIN_ID,
)
from db import (
    db_init,
    db_get_opener,
    db_set_opener,
    db_set_prompt,
)
from helpers import is_staff, message_contains_trigger, build_staff_ping
from privatka import ensure_private_setup_message, PrivateSetupView
from tickets import resolve_ticket_opener_fallback, is_ignored_ticket_opener_id, is_ignored_ticket_opener_member
from ui import TicketDecisionView


# ==========================================================
#                        EVENTS
# ==========================================================

_ID_RE = re.compile(r"<@!?(\d{15,25})>|\b(\d{15,25})\b")


def _extract_user_ids(text: str) -> list[int]:
    ids: list[int] = []
    for m in _ID_RE.finditer(text or ""):
        g1 = m.group(1)
        g2 = m.group(2)
        raw = g1 or g2
        if raw:
            try:
                ids.append(int(raw))
            except ValueError:
                pass
    # убираем дубли, сохраняя порядок
    out = []
    seen = set()
    for x in ids:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def _is_simple_id_message(text: str) -> bool:
    """True если в тексте по сути только ID/упоминания (можно несколько), без лишних слов."""
    if not text:
        return False
    # убираем все ID/mentions
    cleaned = _ID_RE.sub("", text)
    # разрешаем только пробелы и разделители
    cleaned = cleaned.strip().replace(",", "").replace(";", "").replace("|", "")
    return cleaned.strip() == ""


@client.event
async def on_ready():
    db_init()
    print(f"Logged in as {client.user} (ID: {client.user.id})")

    # persistent views (работают после рестарта)
    client.add_view(TicketDecisionView())
    client.add_view(PrivateSetupView())

    # сообщение с кнопкой в приватке (если бот там есть и имеет доступ)
    await ensure_private_setup_message()

    # ------------------------------------------------------
    # Slash-команды: делаем "по красоте" — регистрируем в КАЖДОЙ гильдии как guild commands.
    # Почему так:
    #   - Global commands могут появляться с задержкой (иногда минуты/часы).
    #   - Guild commands появляются почти сразу.
    # Ключевой момент: наши /add /del /menu объявлены как GLOBAL, поэтому перед sync
    # копируем их в гильдию через copy_global_to().
    # Важно: если бот был приглашён БЕЗ scope "applications.commands", команды НЕ появятся.
    # ------------------------------------------------------
    global_cmds = tree.get_commands()
    print(f"[SlashSync] global_commands_loaded={len(global_cmds)}")

    total_synced = 0
    for g in list(client.guilds):
        try:
            tree.copy_global_to(guild=discord.Object(id=g.id))
            synced = await tree.sync(guild=discord.Object(id=g.id))
            total_synced += len(synced)
            print(f"[SlashSync] guild={g.name} ({g.id}) synced={len(synced)}")
        except discord.HTTPException as e:
            print(f"[SlashSync] guild={g.name} ({g.id}) FAILED: {e}")
        except Exception as e:
            print(f"[SlashSync] guild={g.name} ({g.id}) FAILED: {e}")
    print(f"[SlashSync] total_synced={total_synced}")


@client.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel) and channel.category_id == TICKETS_CATEGORY_ID:
        await asyncio.sleep(2)
        try:
            msg = await channel.send(WELCOME_MESSAGE)
            # Автозакреп (нужны права Manage Messages)
            try:
                await msg.pin(reason="[SH] Auto-pin application template")
            except (discord.Forbidden, discord.HTTPException):
                pass
        except discord.HTTPException:
            pass


@client.event
async def on_message(message: discord.Message):
    # игнор своих сообщений
    if message.author and client.user and message.author.id == client.user.id:
        return

    # ------------------------------------------------------
    # Админская ручная синхронизация slash-команд (на случай, если хостинг/рестарт и т.п.)
    # Работает и в ЛС, и в любом канале.
    # ------------------------------------------------------
    if message.author and message.author.id == IGNORE_ADD_ADMIN_ID and message.content:
        cmd = message.content.strip().lower()
        if cmd in {"!sync", "!resync"}:
            results = []
            for g in list(client.guilds):
                try:
                    tree.copy_global_to(guild=discord.Object(id=g.id))
                    synced = await tree.sync(guild=discord.Object(id=g.id))
                    results.append(f"{g.name}: {len(synced)}")
                except Exception as e:
                    results.append(f"{g.name}: FAIL ({type(e).__name__})")
            try:
                await message.channel.send(
                    "🔁 Sync done. " + " | ".join(results),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass
            return


    # дальше нас интересуют только сообщения на сервере, в текстовых каналах
    if not message.guild or not isinstance(message.channel, discord.TextChannel):
        return

    # только тикет-каналы
    if message.channel.category_id != TICKETS_CATEGORY_ID:
        return

    # 1) сохраняем opener: первый non-bot пользователь, который НЕ staff и НЕ в игноре
    if isinstance(message.author, discord.Member) and not message.author.bot:
        if (not is_staff(message.author)) and (not is_ignored_ticket_opener_member(message.author)):
            if db_get_opener(message.channel.id) is None:
                db_set_opener(message.channel.id, message.author.id)

    # 2) триггер Ticket Tool
    if not message_contains_trigger(message):
        return

    # защита от подделки: игрок не должен запускать кнопки обычным сообщением
    # (разрешаем ботов и вебхуки)
    if not message.author.bot and message.webhook_id is None:
        return

    # анти-спам
    now = time.time()
    last = _last_prompt_time.get(message.channel.id, 0.0)
    if now - last < PROMPT_COOLDOWN_SECONDS:
        return
    _last_prompt_time[message.channel.id] = now

    # если opener не успели записать — попробуем фоллбеком
    if db_get_opener(message.channel.id) is None:
        opener = await resolve_ticket_opener_fallback(message.channel)
        if opener:
            if isinstance(opener, discord.Member):
                if not is_ignored_ticket_opener_member(opener):
                    db_set_opener(message.channel.id, opener.id)
            else:
                # Если по какой-то причине получили не Member, то проверяем только по ID
                if not is_ignored_ticket_opener_id(opener.id):
                    db_set_opener(message.channel.id, opener.id)

    staff_ping = build_staff_ping(message.guild)
    spoiler_pings = f"||{staff_ping}||" if staff_ping else ""

    # Отступ как просили: текст -> пустая строка -> ||пинги||
    prompt_text = (
        "**Если вы хотите закрыть заявку с причиной/комментарием, нажмите на нужную кнопку ниже. "
        "Пользователь получит сообщение в личные сообщения!**\n\n"
        f"{spoiler_pings}"
    )

    # Небольшая задержка после создания тикета, чтобы Discord успел прогрузить права/интеграции.
    # Важно: иногда сразу после авто-создания канала/прав первая отправка может падать.
    # Поэтому делаем быстрый first-try и несколько коротких ретраев.
    for delay in (0, 1, 2):
        if delay:
            await asyncio.sleep(delay)
        try:
            sent = await message.channel.send(
                prompt_text,
                view=TicketDecisionView(),
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
            db_set_prompt(message.channel.id, sent.id)
            break
        except (discord.Forbidden, discord.HTTPException):
            continue

