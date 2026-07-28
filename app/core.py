from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

bot = Bot(settings.bot_token)
scheduler = AsyncIOScheduler(timezone=settings.timezone)
