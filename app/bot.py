from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.core import bot
from app.handlers.admin import router as admin_router
from app.handlers.payments import router as payments_router
from app.handlers.user import router as user_router

dp = Dispatcher(storage=MemoryStorage())
dp.include_router(payments_router)
dp.include_router(admin_router)
dp.include_router(user_router)
