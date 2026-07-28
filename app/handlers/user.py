import html
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.config import settings
from app.database import connect, now_ts
from app.keyboards import payment_methods, main_menu
from app.services.products import already_today, has_access, issue_products
from app.utils import brand_header, product_caption

router = Router()


class Preferences(StatesGroup):
    categories = State()
    budget = State()


class Promo(StatesGroup):
    code = State()


async def register(message: Message, referrer_id: int | None = None) -> None:
    user = message.from_user
    if referrer_id == user.id:
        referrer_id = None
    db = await connect()
    try:
        exists = await (await db.execute("SELECT 1 FROM users WHERE user_id=?", (user.id,))).fetchone()
        if exists:
            await db.execute("""
                UPDATE users SET username=?, full_name=?, last_seen=?, blocked=0 WHERE user_id=?
            """, (user.username or "", user.full_name or "", now_ts(), user.id))
        else:
            await db.execute("""
                INSERT INTO users(user_id, username, full_name, created_at, last_seen, referrer_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user.id, user.username or "", user.full_name or "", now_ts(), now_ts(), referrer_id))
        await db.commit()
    finally:
        await db.close()



async def send_home(message_or_callback) -> None:
    target = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback

    # Удаляем старую обычную клавиатуру Telegram.
    await target.answer(
        "Обновляем меню…",
        reply_markup=ReplyKeyboardRemove(),
    )

    await target.answer(
        "⚡️ <b>DT TEAM</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>ТОВАРЫ ДЛЯ ПЕРЕПРОДАЖИ</b>\n\n"
        f"Каждый день вы получаете <b>{settings.products_per_day} персональных товара</b> "
        "без повторов.\n\n"
        f"💎 Доступ: <b>{settings.access_days} дней</b>\n"
        f"💰 Стоимость: <b>{settings.price_usdt} USDT</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    referrer = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_") and parts[1][4:].isdigit():
        referrer = int(parts[1][4:])
    await register(message, referrer)
    await send_home(message)


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_home(callback)


@router.callback_query(F.data == "menu:buy")
async def menu_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    if not settings.crypto_pay_enabled and not settings.xrocket_enabled:
        await callback.message.answer("Оплата временно недоступна.")
        return
    await callback.message.answer(
        "⚡️ <b>DT TEAM — ОПЛАТА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Сумма: <b>{settings.price_usdt} USDT</b>\n\n"
        "Выберите удобный способ оплаты:",
        parse_mode="HTML",
        reply_markup=payment_methods(),
    )


@router.callback_query(F.data == "menu:products")
async def menu_products(callback: CallbackQuery) -> None:
    await callback.answer()
    if not await has_access(callback.from_user.id):
        await callback.message.answer(
            "🔒 <b>Активного доступа нет</b>\n\n"
            "Оформите доступ, чтобы получать персональные товары.",
            parse_mode="HTML",
            reply_markup=payment_methods(),
        )
        return
    sent = await issue_products(callback.from_user.id)
    if not sent:
        if await already_today(callback.from_user.id) >= settings.products_per_day:
            await callback.message.answer("✅ Сегодняшние товары уже получены.", reply_markup=main_menu())
        else:
            await callback.message.answer("В базе пока недостаточно новых товаров.", reply_markup=main_menu())


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery) -> None:
    await callback.answer()
    db = await connect()
    try:
        user = await (await db.execute("SELECT * FROM users WHERE user_id=?", (callback.from_user.id,))).fetchone()
        count = await (await db.execute(
            "SELECT COUNT(*) c FROM assignments WHERE user_id=?", (callback.from_user.id,)
        )).fetchone()
        sold = await (await db.execute(
            "SELECT COUNT(*) c FROM feedback WHERE user_id=? AND status='sold'", (callback.from_user.id,)
        )).fetchone()
    finally:
        await db.close()

    active = bool(user and user["access_until"] > now_ts())
    until = datetime.fromtimestamp(user["access_until"], settings.timezone).strftime("%d.%m.%Y %H:%M") if active else "—"
    await callback.message.answer(
        "⚡️ <b>DT TEAM — ПРОФИЛЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Статус: <b>{'активен' if active else 'нет доступа'}</b>\n"
        f"Доступ до: <b>{until}</b>\n"
        f"Получено товаров: <b>{count['c']}</b>\n"
        f"Продано: <b>{sold['c']}</b>",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:archive")
async def menu_archive(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_collection(callback.message, """
        SELECT p.* FROM assignments a JOIN products p ON p.id=a.product_id
        WHERE a.user_id=? ORDER BY a.delivered_at DESC
    """, (callback.from_user.id,))


@router.callback_query(F.data == "menu:favorites")
async def menu_favorites(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_collection(callback.message, """
        SELECT p.* FROM favorites f JOIN products p ON p.id=f.product_id
        WHERE f.user_id=? ORDER BY f.created_at DESC
    """, (callback.from_user.id,))


@router.callback_query(F.data == "menu:support")
async def menu_support(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        f"💬 Поддержка: {settings.support_username}",
        reply_markup=main_menu(),
    )


@router.message(F.text == "💎 Купить доступ")
async def buy(message: Message) -> None:
    if not settings.crypto_pay_enabled and not settings.xrocket_enabled:
        await message.answer("Оплата временно недоступна.")
        return
    await message.answer(
        brand_header("ВЫБЕРИТЕ СПОСОБ ОПЛАТЫ") +
        f"\nСумма: <b>{settings.price_usdt} USDT</b>",
        parse_mode="HTML",
        reply_markup=payment_methods(),
    )


@router.message(F.text == "🔥 Получить товары")
async def get_products(message: Message) -> None:
    if not await has_access(message.from_user.id):
        await message.answer("Активного доступа нет.", reply_markup=payment_methods())
        return
    sent = await issue_products(message.from_user.id)
    if not sent:
        if await already_today(message.from_user.id) >= settings.products_per_day:
            await message.answer("Сегодняшние товары уже получены.")
        else:
            await message.answer("В базе пока недостаточно новых товаров.")


@router.message(F.text == "👤 Профиль")
async def profile(message: Message) -> None:
    db = await connect()
    try:
        user = await (await db.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))).fetchone()
        count = await (await db.execute(
            "SELECT COUNT(*) c FROM assignments WHERE user_id=?", (message.from_user.id,)
        )).fetchone()
        sold = await (await db.execute(
            "SELECT COUNT(*) c FROM feedback WHERE user_id=? AND status='sold'", (message.from_user.id,)
        )).fetchone()
    finally:
        await db.close()

    active = bool(user and user["access_until"] > now_ts())
    until = datetime.fromtimestamp(user["access_until"], settings.timezone).strftime("%d.%m.%Y %H:%M") if active else "—"
    await message.answer(
        brand_header("ПРОФИЛЬ") +
        f"\nСтатус: <b>{'активен' if active else 'нет доступа'}</b>\n"
        f"Доступ до: <b>{until}</b>\n"
        f"Получено товаров: <b>{count['c']}</b>\n"
        f"Отмечено проданными: <b>{sold['c']}</b>",
        parse_mode="HTML",
    )


async def show_collection(message: Message, query: str, params: tuple) -> None:
    db = await connect()
    try:
        rows = await (await db.execute(query, params)).fetchall()
    finally:
        await db.close()
    if not rows:
        await message.answer("Здесь пока пусто.")
        return
    for product in rows[:20]:
        kwargs = dict(
            chat_id=message.from_user.id,
            caption=product_caption(product),
            parse_mode="HTML",
        )
        if product["image_type"] == "document":
            await message.bot.send_document(document=product["image_file_id"], **kwargs)
        else:
            await message.bot.send_photo(photo=product["image_file_id"], **kwargs)


@router.message(F.text == "📚 Архив")
async def archive(message: Message) -> None:
    await show_collection(message, """
        SELECT p.* FROM assignments a JOIN products p ON p.id=a.product_id
        WHERE a.user_id=? ORDER BY a.delivered_at DESC
    """, (message.from_user.id,))


@router.message(F.text == "⭐ Избранное")
async def favorites(message: Message) -> None:
    await show_collection(message, """
        SELECT p.* FROM favorites f JOIN products p ON p.id=f.product_id
        WHERE f.user_id=? ORDER BY f.created_at DESC
    """, (message.from_user.id,))


@router.callback_query(F.data.startswith("fav:"))
async def favorite(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[1])
    db = await connect()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO favorites(user_id, product_id, created_at) VALUES (?, ?, ?)",
            (callback.from_user.id, product_id, now_ts()),
        )
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Добавлено в избранное.", show_alert=True)


@router.callback_query(F.data.startswith("feedback:"))
async def feedback(callback: CallbackQuery) -> None:
    _, status, product_id = callback.data.split(":")
    db = await connect()
    try:
        await db.execute("""
            INSERT INTO feedback(user_id, product_id, status, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, product_id) DO UPDATE SET
                status=excluded.status, created_at=excluded.created_at
        """, (callback.from_user.id, int(product_id), status, now_ts()))
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Ответ сохранён.", show_alert=True)


@router.message(F.text == "__disabled_settings__")
async def preferences_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Preferences.categories)
    await message.answer("Введите категории через запятую или отправьте «Все».")


@router.message(Preferences.categories)
async def preferences_categories(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    categories = [] if text.lower() == "все" else [x.strip() for x in text.split(",") if x.strip()]
    await state.update_data(categories=categories)
    await state.set_state(Preferences.budget)
    await message.answer("Введите бюджет, например 100-200, или «Без ограничений».")


@router.message(Preferences.budget)
async def preferences_budget(message: Message, state: FSMContext) -> None:
    import re
    text = (message.text or "").strip().lower()
    low = high = None
    if text != "без ограничений":
        match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)\s*", text)
        if not match:
            await message.answer("Формат: 100-200 или «Без ограничений».")
            return
        low, high = (float(x.replace(",", ".")) for x in match.groups())
        low, high = min(low, high), max(low, high)

    data = await state.get_data()
    db = await connect()
    try:
        await db.execute("""
            INSERT INTO user_preferences(user_id, categories, budget_min, budget_max, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET categories=excluded.categories,
                budget_min=excluded.budget_min, budget_max=excluded.budget_max,
                updated_at=excluded.updated_at
        """, (message.from_user.id, ",".join(data["categories"]), low, high, now_ts()))
        await db.commit()
    finally:
        await db.close()
    await state.clear()
    await message.answer("✅ Настройки сохранены.")


@router.message(F.text == "__disabled_referral__")
async def referral(message: Message) -> None:
    me = await message.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"За оплатившего друга начисляется {settings.referral_bonus_days} день доступа.\n\n<code>{link}</code>",
        parse_mode="HTML",
    )


@router.message(F.text == "🎟 Промокод")
async def promo_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Promo.code)
    await message.answer("Введите промокод.")


@router.message(Promo.code)
async def promo_use(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    db = await connect()
    try:
        promo = await (await db.execute(
            "SELECT * FROM promo_codes WHERE code=? AND active=1", (code,)
        )).fetchone()
        used = await (await db.execute(
            "SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, message.from_user.id)
        )).fetchone()
        if not promo or promo["used_count"] >= promo["max_uses"] or used:
            await message.answer("Промокод недействителен или уже использован.")
            await state.clear()
            return
        current = await (await db.execute(
            "SELECT access_until FROM users WHERE user_id=?", (message.from_user.id,)
        )).fetchone()
        until = max(now_ts(), current["access_until"]) + promo["days"] * 86400
        await db.execute("UPDATE users SET access_until=? WHERE user_id=?", (until, message.from_user.id))
        await db.execute("INSERT INTO promo_uses VALUES (?, ?, ?)", (code, message.from_user.id, now_ts()))
        await db.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
        await db.commit()
    finally:
        await db.close()
    await state.clear()
    await message.answer("✅ Промокод активирован.")


@router.message(F.text == "💬 Поддержка")
async def support(message: Message) -> None:
    await message.answer(f"Поддержка: {settings.support_username}")
