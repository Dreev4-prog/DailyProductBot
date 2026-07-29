import asyncio
import logging

import uvicorn
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

from app.bot import bot, dp
from app.core import scheduler
from app.config import settings
from app.database import init_db
from app.services.payments import validate_payment_connections
from app.services.scheduler import configure_scheduler
from app.web.app import web_app


async def configure_telegram_menu() -> None:
    commands = [
        BotCommand(command="start", description="Открыть главное меню"),
        BotCommand(command="admin", description="Админ-панель"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def run_bot() -> None:
    await init_db()
    await configure_telegram_menu()
    await validate_payment_connections()
    configure_scheduler()
    scheduler.start()
    await dp.start_polling(bot)


async def run_web() -> None:
    if not settings.web_admin_enabled:
        while True:
            await asyncio.sleep(3600)
    config = uvicorn.Config(
        web_app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    await asyncio.gather(run_bot(), run_web())


if __name__ == "__main__":
    asyncio.run(main())
