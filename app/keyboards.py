from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Получить товары", callback_data="menu:products")],
        [
            InlineKeyboardButton(text="💎 Купить доступ", callback_data="menu:buy"),
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile"),
        ],
        [
            InlineKeyboardButton(text="📚 Архив", callback_data="menu:archive"),
            InlineKeyboardButton(text="⭐ Избранное", callback_data="menu:favorites"),
        ],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="menu:support")],
    ])


def payment_methods() -> InlineKeyboardMarkup:
    rows = []
    if settings.crypto_pay_enabled:
        rows.append([
            InlineKeyboardButton(
                text="💎 Оплатить через Crypto Bot",
                callback_data="pay:create:crypto"
            )
        ])
    if settings.xrocket_enabled:
        rows.append([
            InlineKeyboardButton(
                text="🚀 Оплатить через xRocket",
                callback_data="pay:create:xrocket"
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")])
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
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:home")],
    ])
