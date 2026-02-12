# main.py
"""Bot entrypoint.

This bot is deployed on Linux hostings where:
- stdout/stderr may be buffered (logs appear late),
- the process can look "stuck" at "logging in using static token".

To make deployments predictable we:
- enable line-buffered stdout/stderr,
- enable discord.py logging,
- optionally force IPv4 DNS resolution (DISCORD_FORCE_IPV4=1),
- start the client as a task and watch for READY with a timeout.

Environment options:
  DISCORD_READY_TIMEOUT: seconds to wait for on_ready (default: 45)
  DISCORD_FORCE_IPV4: 1 to force IPv4 DNS resolution (default: 0)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord

from config import TOKEN
from app import client

# Important: registering handlers
import events  # noqa: F401
import slash_commands  # noqa: F401


def _enable_line_buffered_io() -> None:
    """Force logs to appear immediately in most hosting panels."""
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        os.environ.setdefault("PYTHONUNBUFFERED", "1")


def _maybe_force_ipv4() -> None:
    if str(os.getenv("DISCORD_FORCE_IPV4", "0")).lower() in {"1", "true", "yes", "on"}:
        try:
            import socket

            _orig_getaddrinfo = socket.getaddrinfo

            def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
                return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

            socket.getaddrinfo = _ipv4_getaddrinfo  # type: ignore[assignment]
            logging.info("🔧 DISCORD_FORCE_IPV4=1 (forcing IPv4)")
        except Exception as e:
            logging.warning("DISCORD_FORCE_IPV4 failed: %s", e)


async def _run() -> None:
    _enable_line_buffered_io()

    # Enable discord.py logs (gateway/connect problems become visible)
    try:
        discord.utils.setup_logging(level=logging.INFO)
    except Exception:
        logging.basicConfig(level=logging.INFO)

    _maybe_force_ipv4()

    timeout_s = float(os.getenv("DISCORD_READY_TIMEOUT", "45") or "45")

    start_task = asyncio.create_task(client.start(TOKEN))
    ready_task = asyncio.create_task(client.wait_until_ready())

    try:
        done, _pending = await asyncio.wait(
            {start_task, ready_task},
            timeout=timeout_s,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if not done:
            raise asyncio.TimeoutError()

        # If start_task finished first, it either crashed or exited.
        if start_task in done:
            exc = start_task.exception()
            if exc:
                raise exc
            raise RuntimeError("Discord client stopped before READY")

        # READY reached
        logging.info("✅ Discord READY")

        # Stop the watchdog task (READY is a one-time event)
        if not ready_task.done():
            ready_task.cancel()

        # Keep running until the bot is stopped
        await start_task

    except asyncio.TimeoutError:
        logging.error(
            "❌ Не дождались READY за %.0f сек. "
            "Обычно это означает проблему сети/вебсокета до Discord Gateway на хостинге.",
            timeout_s,
        )
        try:
            await client.close()
        finally:
            start_task.cancel()
        raise
    except discord.LoginFailure:
        logging.exception("❌ Токен бота неверный или отозван (LoginFailure).")
        raise
    except discord.PrivilegedIntentsRequired:
        logging.exception(
            "❌ Включите Privileged Gateway Intents в Discord Developer Portal "
            "(обычно Message Content Intent).",
        )
        raise
    except Exception:
        logging.exception("❌ Критическая ошибка запуска клиента Discord.")
        raise


if __name__ == "__main__":
    asyncio.run(_run())
