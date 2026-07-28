from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.database import connect, now_ts
from app.keyboards import admin_menu
from app.services.access import activate_access
from app.services.products import daily_distribution
from app.utils import brand_header, parse_price

router = Router()


class AddProduct(StatesGroup):
    category = State()
    title = State()
    description = State()
    image = State()
    price = State()


class Broadcast(StatesGroup):
    message = State()


def admin_only(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(Command("admin"))
async def admin(message: Message) -> None:
    if admin_only(message.from_user.id):
        await message.answer(
            brand_header("ПАНЕЛЬ АДМИНИСТРАТОРА"),
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )


@router.callback_query(F.data == "admin:add")
async def add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AddProduct.category)
    await callback.message.answer("Категория товара:")


@router.message(AddProduct.category)
async def add_category(message: Message, state: FSMContext) -> None:
    await state.update_data(category=(message.text or "").strip())
    await state.set_state(AddProduct.title)
    await message.answer("Название товара:")


@router.message(AddProduct.title)
async def add_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=(message.text or "").strip())
    await state.set_state(AddProduct.description)
    await message.answer("Описание товара:")


@router.message(AddProduct.description)
async def add_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(AddProduct.image)
    await message.answer("Отправьте картинку товара:")


@router.message(AddProduct.image, F.photo | F.document)
async def add_image(message: Message, state: FSMContext) -> None:
    if message.photo:
        file_id, image_type = message.photo[-1].file_id, "photo"
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id, image_type = message.document.file_id, "document"
    else:
        await message.answer("Нужно отправить изображение.")
        return
    await state.update_data(image_file_id=file_id, image_type=image_type)
    await state.set_state(AddProduct.price)
    await message.answer("Цена товара:")


@router.message(AddProduct.image)
async def image_required(message: Message) -> None:
    await message.answer("Отправьте картинку как фото или файл.")


@router.message(AddProduct.price)
async def add_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    price = (message.text or "").strip()
    db = await connect()
    try:
        await db.execute("""
            INSERT INTO products(category, title, description, image_file_id,
                                 image_type, price_text, price_num, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (
            data["category"], data["title"], data["description"],
            data["image_file_id"], data["image_type"],
            price, parse_price(price), now_ts(),
        ))
        await db.commit()
    finally:
        await db.close()
    await state.clear()
    await message.answer("✅ Товар добавлен.", reply_markup=admin_menu())


@router.callback_query(F.data == "admin:products")
async def product_stats(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    db = await connect()
    try:
        total = await (await db.execute("SELECT COUNT(*) c FROM products WHERE active=1")).fetchone()
    finally:
        await db.close()
    await callback.answer()
    await callback.message.answer(f"Активных товаров: {total['c']}")


@router.callback_query(F.data == "admin:users")
async def users(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    db = await connect()
    try:
        total = await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone()
        active = await (await db.execute(
            "SELECT COUNT(*) c FROM users WHERE access_until>?", (now_ts(),)
        )).fetchone()
    finally:
        await db.close()
    await callback.answer()
    await callback.message.answer(f"Пользователей: {total['c']}\nАктивных: {active['c']}")


@router.callback_query(F.data == "admin:stats")
async def stats(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    db = await connect()
    try:
        users = await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone()
        products = await (await db.execute("SELECT COUNT(*) c FROM products WHERE active=1")).fetchone()
        assignments = await (await db.execute("SELECT COUNT(*) c FROM assignments")).fetchone()
        sold = await (await db.execute("SELECT COUNT(*) c FROM feedback WHERE status='sold'")).fetchone()
        revenue = await (await db.execute("""
            SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) total
            FROM invoices WHERE activated=1
        """)).fetchone()
    finally:
        await db.close()
    await callback.answer()
    await callback.message.answer(
        brand_header("АНАЛИТИКА") +
        f"\nПользователей: <b>{users['c']}</b>\n"
        f"Товаров: <b>{products['c']}</b>\n"
        f"Выдач: <b>{assignments['c']}</b>\n"
        f"Продано: <b>{sold['c']}</b>\n"
        f"Выручка: <b>{revenue['total']} USDT</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:send")
async def send_now(callback: CallbackQuery) -> None:
    if admin_only(callback.from_user.id):
        await callback.answer("Запущено")
        await daily_distribution()
        await callback.message.answer("✅ Выдача завершена.")


@router.message(Command("sendtoday"))
async def send_today(message: Message) -> None:
    if admin_only(message.from_user.id):
        await daily_distribution()
        await message.answer("✅ Выдача завершена.")


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if admin_only(callback.from_user.id):
        await callback.answer()
        await state.set_state(Broadcast.message)
        await callback.message.answer("Отправьте сообщение для рассылки.")


@router.message(Broadcast.message)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if not admin_only(message.from_user.id):
        return
    db = await connect()
    try:
        users = await (await db.execute("SELECT user_id FROM users WHERE blocked=0")).fetchall()
    finally:
        await db.close()
    sent = 0
    for user in users:
        try:
            await message.bot.copy_message(user["user_id"], message.chat.id, message.message_id)
            sent += 1
        except Exception:
            pass
    await state.clear()
    await message.answer(f"Доставлено: {sent}")


@router.message(Command("grant"))
async def grant(message: Message) -> None:
    if not admin_only(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Формат: /grant USER_ID DAYS")
        return
    await activate_access(int(parts[1]), int(parts[2]))
    await message.answer("✅ Доступ выдан.")


@router.message(Command("promo"))
async def promo(message: Message) -> None:
    if not admin_only(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await message.answer("Формат: /promo CODE DAYS USES")
        return
    db = await connect()
    try:
        await db.execute("""
            INSERT OR REPLACE INTO promo_codes(code, days, max_uses, used_count, active, created_at)
            VALUES (?, ?, ?, 0, 1, ?)
        """, (parts[1].upper(), int(parts[2]), int(parts[3]), now_ts()))
        await db.commit()
    finally:
        await db.close()
    await message.answer("✅ Промокод создан.")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Операция отменена.")
