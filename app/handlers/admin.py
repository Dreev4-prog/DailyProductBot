from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

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


class EditProduct(StatesGroup):
    category = State()
    title = State()
    description = State()
    image = State()
    price = State()


class Broadcast(StatesGroup):
    message = State()


def admin_only(user_id: int) -> bool:
    return user_id in settings.admin_ids


def products_list_keyboard(rows, page: int, trash: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    prefix = "trashprod" if trash else "product"
    for row in rows:
        status_icon = "🟢" if row["active"] else "⚫️"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} #{row['id']} {row['title'][:32]}",
                callback_data=f"admin:{prefix}:{row['id']}"
            )
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin:{'trash' if trash else 'products'}:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"Стр. {page+1}", callback_data="admin:noop"))
    if len(rows) == 10:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin:{'trash' if trash else 'products'}:{page+1}"))
    buttons.append(nav)
    if trash:
        buttons.append([InlineKeyboardButton(text="↩️ К товарам", callback_data="admin:products:0")])
    else:
        buttons.append([InlineKeyboardButton(text="🗑 Корзина", callback_data="admin:trash:0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def product_manage_keyboard(product_id: int, active: int, deleted: bool = False) -> InlineKeyboardMarkup:
    if deleted:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="♻️ Восстановить", callback_data=f"admin:restore:{product_id}")],
            [InlineKeyboardButton(text="❌ Удалить навсегда", callback_data=f"admin:harddelete_confirm:{product_id}")],
            [InlineKeyboardButton(text="⬅️ Корзина", callback_data="admin:trash:0")],
        ])
    toggle_text = "🚫 Скрыть" if active else "✅ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"admin:edit:{product_id}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin:toggle:{product_id}")],
        [InlineKeyboardButton(text="🗑 В корзину", callback_data=f"admin:delete_confirm:{product_id}")],
        [InlineKeyboardButton(text="⬅️ К товарам", callback_data="admin:products:0")],
    ])


async def fetch_product(product_id: int):
    db = await connect()
    try:
        return await (await db.execute("SELECT * FROM products WHERE id=?", (product_id,))).fetchone()
    finally:
        await db.close()


async def show_product_card(callback: CallbackQuery, product_id: int) -> None:
    product = await fetch_product(product_id)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    deleted = product["deleted_at"] is not None
    card_text = (
        f"📦 <b>Товар #{product['id']}</b>\n\n"
        f"Категория: <b>{product['category']}</b>\n"
        f"Название: <b>{product['title']}</b>\n"
        f"Описание: {product['description']}\n"
        f"Цена: <b>{product['price_text']}</b>\n"
        f"Статус: <b>{'В корзине' if deleted else ('Активен' if product['active'] else 'Скрыт')}</b>"
    )

    await callback.message.answer(
        card_text,
        parse_mode="HTML",
        reply_markup=product_manage_keyboard(
            product["id"],
            product["active"],
            deleted,
        ),
    )


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


@router.callback_query((F.data == "admin:products") | F.data.startswith("admin:products:"))
async def product_stats(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    db = await connect()
    try:
        rows = await (await db.execute("""
            SELECT * FROM products
            WHERE deleted_at IS NULL
            ORDER BY id DESC
            LIMIT 10 OFFSET ?
        """, (page * 10,))).fetchall()
    finally:
        await db.close()
    await callback.answer()
    if not rows:
        await callback.message.answer(
            "📦 <b>База товаров пуста</b>\n\nСначала добавьте товар.",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return
    await callback.message.answer(
        "📦 <b>База товаров</b>\n\nНажмите на товар для управления:",
        parse_mode="HTML",
        reply_markup=products_list_keyboard(rows, page),
    )


@router.callback_query((F.data == "admin:trash") | F.data.startswith("admin:trash:"))
async def trash_list(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    db = await connect()
    try:
        rows = await (await db.execute("""
            SELECT * FROM products
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            LIMIT 10 OFFSET ?
        """, (page * 10,))).fetchall()
    finally:
        await db.close()
    await callback.answer()
    if not rows:
        await callback.message.answer(
            "🗑 <b>Корзина пуста</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К товарам", callback_data="admin:products:0")]
            ]),
        )
        return
    await callback.message.answer(
        "🗑 <b>Корзина товаров</b>\n\nВыберите товар:",
        parse_mode="HTML",
        reply_markup=products_list_keyboard(rows, page, trash=True),
    )


@router.callback_query(F.data.startswith("admin:product:"))
async def product_card(callback: CallbackQuery) -> None:
    if admin_only(callback.from_user.id):
        await callback.answer()
        await show_product_card(callback, int(callback.data.split(":")[2]))


@router.callback_query(F.data.startswith("admin:trashprod:"))
async def trash_product_card(callback: CallbackQuery) -> None:
    if admin_only(callback.from_user.id):
        await callback.answer()
        await show_product_card(callback, int(callback.data.split(":")[2]))



@router.callback_query(F.data.startswith("admin:edit:"))
async def edit_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split(":")[2])
    product = await fetch_product(product_id)
    if not product or product["deleted_at"] is not None:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    await state.clear()
    await state.set_state(EditProduct.category)
    await state.update_data(
        product_id=product_id,
        old_image_file_id=product["image_file_id"],
        old_image_type=product["image_type"],
    )
    await callback.answer()
    await callback.message.answer(
        f"✏️ Редактирование товара #{product_id}\n\n"
        f"Текущая категория: {product['category']}\n\n"
        "Введите новую категорию или отправьте «-», чтобы оставить прежнюю."
    )


@router.message(EditProduct.category)
async def edit_category(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    await state.update_data(category=None if value == "-" else value)
    await state.set_state(EditProduct.title)
    await message.answer("Введите новое название или «-», чтобы оставить прежнее.")


@router.message(EditProduct.title)
async def edit_title(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    await state.update_data(title=None if value == "-" else value)
    await state.set_state(EditProduct.description)
    await message.answer("Введите новое описание или «-», чтобы оставить прежнее.")


@router.message(EditProduct.description)
async def edit_description(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    await state.update_data(description=None if value == "-" else value)
    await state.set_state(EditProduct.image)
    await message.answer(
        "Отправьте новую картинку.\n"
        "Чтобы оставить старую, отправьте текст «-»."
    )


@router.message(EditProduct.image, F.photo | F.document)
async def edit_image(message: Message, state: FSMContext) -> None:
    if message.photo:
        file_id, image_type = message.photo[-1].file_id, "photo"
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id, image_type = message.document.file_id, "document"
    else:
        await message.answer("Нужно отправить изображение или «-».")
        return
    await state.update_data(image_file_id=file_id, image_type=image_type)
    await state.set_state(EditProduct.price)
    await message.answer("Введите новую цену или «-», чтобы оставить прежнюю.")


@router.message(EditProduct.image, F.text == "-")
async def edit_keep_image(message: Message, state: FSMContext) -> None:
    await state.update_data(image_file_id=None, image_type=None)
    await state.set_state(EditProduct.price)
    await message.answer("Введите новую цену или «-», чтобы оставить прежнюю.")


@router.message(EditProduct.image)
async def edit_image_invalid(message: Message) -> None:
    await message.answer("Отправьте новую картинку или текст «-».")


@router.message(EditProduct.price)
async def edit_price(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    data = await state.get_data()
    product_id = data["product_id"]

    product = await fetch_product(product_id)
    if not product:
        await state.clear()
        await message.answer("Товар больше не существует.")
        return

    category = data.get("category") or product["category"]
    title = data.get("title") or product["title"]
    description = data.get("description") or product["description"]
    image_file_id = data.get("image_file_id") or data["old_image_file_id"]
    image_type = data.get("image_type") or data["old_image_type"]
    price_text = product["price_text"] if value == "-" else value
    price_num = product["price_num"] if value == "-" else parse_price(value)

    db = await connect()
    try:
        await db.execute("""
            UPDATE products
            SET category=?, title=?, description=?, image_file_id=?,
                image_type=?, price_text=?, price_num=?
            WHERE id=? AND deleted_at IS NULL
        """, (
            category, title, description, image_file_id,
            image_type, price_text, price_num, product_id,
        ))
        await db.commit()
    finally:
        await db.close()

    await state.clear()
    await message.answer(
        f"✅ Товар #{product_id} изменён.",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data.startswith("admin:toggle:"))
async def toggle_product(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    product_id = int(callback.data.split(":")[2])
    db = await connect()
    try:
        await db.execute("""
            UPDATE products SET active=CASE WHEN active=1 THEN 0 ELSE 1 END
            WHERE id=? AND deleted_at IS NULL
        """, (product_id,))
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Статус изменён.")
    await show_product_card(callback, product_id)


@router.callback_query(F.data.startswith("admin:delete_confirm:"))
async def delete_confirm(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    product_id = int(callback.data.split(":")[2])
    await callback.answer()
    await callback.message.edit_text(
        "Переместить товар в корзину?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=f"admin:delete:{product_id}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data=f"admin:product:{product_id}")],
        ]),
    )


@router.callback_query(F.data.startswith("admin:delete:"))
async def soft_delete(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    product_id = int(callback.data.split(":")[2])
    db = await connect()
    try:
        await db.execute(
            "UPDATE products SET deleted_at=?, active=0 WHERE id=?",
            (now_ts(), product_id),
        )
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Товар перемещён в корзину.", show_alert=True)
    await callback.message.edit_text(
        "✅ Товар перемещён в корзину.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 К товарам", callback_data="admin:products:0")],
            [InlineKeyboardButton(text="🗑 Открыть корзину", callback_data="admin:trash:0")],
        ]),
    )


@router.callback_query(F.data.startswith("admin:restore:"))
async def restore_product(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    product_id = int(callback.data.split(":")[2])
    db = await connect()
    try:
        await db.execute(
            "UPDATE products SET deleted_at=NULL, active=1 WHERE id=?",
            (product_id,),
        )
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Товар восстановлен.", show_alert=True)
    await show_product_card(callback, product_id)


@router.callback_query(F.data.startswith("admin:harddelete_confirm:"))
async def hard_delete_confirm(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    product_id = int(callback.data.split(":")[2])
    await callback.answer()
    await callback.message.edit_text(
        "⚠️ Удалить товар навсегда?\n\nБудут удалены также связанные выдачи, избранное и отзывы.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить навсегда", callback_data=f"admin:harddelete:{product_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:trashprod:{product_id}")],
        ]),
    )


@router.callback_query(F.data.startswith("admin:harddelete:"))
async def hard_delete(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    product_id = int(callback.data.split(":")[2])
    db = await connect()
    try:
        await db.execute("DELETE FROM products WHERE id=? AND deleted_at IS NOT NULL", (product_id,))
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Товар удалён навсегда.", show_alert=True)
    await callback.message.edit_text(
        "❌ Товар удалён навсегда.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Вернуться в корзину", callback_data="admin:trash:0")]
        ]),
    )


@router.callback_query(F.data == "admin:noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


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


@router.message(Command("paymentstatus"))
async def payment_status(message: Message) -> None:
    if not admin_only(message.from_user.id):
        return
    crypto = "✅ включён" if settings.crypto_pay_enabled else "❌ выключен"
    xrocket = "✅ включён" if settings.xrocket_enabled else "❌ выключен"
    token = "✅ задан" if settings.xrocket_token else "❌ не задан"
    network = settings.xrocket_network
    await message.answer(
        "💳 <b>Статус платежей</b>\n\n"
        f"Crypto Bot: <b>{crypto}</b>\n"
        f"xRocket: <b>{xrocket}</b>\n"
        f"xRocket token: <b>{token}</b>\n"
        f"xRocket network: <b>{network}</b>",
        parse_mode="HTML",
    )
