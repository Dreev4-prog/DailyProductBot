from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.handlers.admin import router as admin_router
from app.handlers.payments import router as payments_router
from app.handlers.user import router as user_router

bot = Bot(settings.bot_token)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(payments_router)
dp.include_router(admin_router)
dp.include_router(user_router)

scheduler = AsyncIOScheduler(timezone=settings.timezone)
