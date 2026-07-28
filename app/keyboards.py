from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.config import settings


def user_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔥 Получить товары")],
            [KeyboardButton(text="💎 Купить доступ"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="📚 Архив"), KeyboardButton(text="⭐ Избранное")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🎁 Пригласить друга")],
            [KeyboardButton(text="🎟 Промокод"), KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True,
    )


def payment_methods() -> InlineKeyboardMarkup:
    rows = []
    if settings.crypto_pay_enabled:
        rows.append([InlineKeyboardButton(text="💎 Crypto Bot", callback_data="pay:create:crypto")])
    if settings.xrocket_enabled:
        rows.append([InlineKeyboardButton(text="🚀 xRocket", callback_data="pay:create:xrocket")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin:add")],
        [InlineKeyboardButton(text="📦 База товаров", callback_data="admin:products")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users")],
        [InlineKeyboardButton(text="🎲 Раздать сейчас", callback_data="admin:send")],
        [InlineKeyboardButton(text="📊 Аналитика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
    ])


def product_actions(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav:{product_id}"),
            InlineKeyboardButton(text="✅ Продал", callback_data=f"feedback:sold:{product_id}"),
        ],
        [
            InlineKeyboardButton(text="🕐 Ещё продаю", callback_data=f"feedback:selling:{product_id}"),
            InlineKeyboardButton(text="❌ Не подошёл", callback_data=f"feedback:skip:{product_id}"),
        ],
    ])
