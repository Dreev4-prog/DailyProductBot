import asyncio
import logging

import uvicorn

from app.bot import bot, dp
from app.core import scheduler
from app.config import settings
from app.database import init_db
from app.services.payments import validate_payment_connections
from app.services.scheduler import configure_scheduler
from app.web.app import web_app


async def run_bot() -> None:
    await init_db()
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
