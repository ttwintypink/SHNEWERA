# main.py
from config import TOKEN
from app import client
import events  # noqa: F401  (важно: регистрирует handlers)
import slash_commands  # noqa: F401  (важно: исторически тут были slash-команды; сейчас файл пустой)

client.run(TOKEN)
