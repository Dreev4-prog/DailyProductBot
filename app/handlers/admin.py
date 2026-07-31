from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.database import connect, now_ts, get_bot_setting, set_bot_setting
import sqlite3
from app.keyboards import admin_menu, category_keyboard, PRODUCT_CATEGORIES
from app.services.access import activate_access
from app.services.media import get_product_images, save_product_images, send_product_gallery
from app.services.search import find_similar_products, search_products
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
    confirm = State()


class AddAdmin(StatesGroup):
    user_id = State()


class EditWelcome(StatesGroup):
    text = State()


class ProductSearch(StatesGroup):
    query = State()


class DuplicateReview(StatesGroup):
    decision = State()


def admin_only(user_id: int) -> bool:
    if user_id in settings.admin_ids:
        return True
    try:
        with sqlite3.connect(settings.database_path) as db:
            row = db.execute(
                "SELECT 1 FROM bot_admins WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return row is not None
    except sqlite3.Error:
        return False


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Приветственный текст", callback_data="admin:welcome_text")],
        [InlineKeyboardButton(text="👤 Администраторы", callback_data="admin:admins")],
        [InlineKeyboardButton(text="💳 Проверить платежи", callback_data="admin:payment_status")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:panel")],
    ])


def admins_keyboard(rows) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить администратора", callback_data="admin:add_admin")],
    ]
    for row in rows:
        user_id = int(row["user_id"])
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ Удалить {user_id}",
                callback_data=f"admin:remove_admin_confirm:{user_id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Настройки", callback_data="admin:settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
        buttons.append([InlineKeyboardButton(text="🔍 Поиск и фильтры", callback_data="admin:products_hub")])
        buttons.append([InlineKeyboardButton(text="🗑 Корзина", callback_data="admin:trash:0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def products_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск товара", callback_data="admin:product_search")],
        [
            InlineKeyboardButton(text="💰 50–100 €", callback_data="admin:product_filter:50_100:0"),
            InlineKeyboardButton(text="💎 100–200 €", callback_data="admin:product_filter:100_200:0"),
        ],
        [InlineKeyboardButton(text="👑 200–500 €", callback_data="admin:product_filter:200_500:0")],
        [
            InlineKeyboardButton(text="🟢 Активные", callback_data="admin:product_filter:active:0"),
            InlineKeyboardButton(text="⚫️ Скрытые", callback_data="admin:product_filter:hidden:0"),
        ],
        [InlineKeyboardButton(text="📈 Популярные", callback_data="admin:popular_products")],
        [InlineKeyboardButton(text="🗑 Корзина", callback_data="admin:trash:0")],
        [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="admin:panel")],
    ])


def duplicate_review_keyboard(rows) -> InlineKeyboardMarkup:
    buttons = []
    for row in rows[:5]:
        buttons.append([InlineKeyboardButton(text=f"👁 #{row['id']} {row['title'][:28]}", callback_data=f"admin:duplicate_open:{row['id']}")])
    buttons.extend([
        [InlineKeyboardButton(text="➕ Всё равно добавить", callback_data="admin:duplicate_continue")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin:duplicate_cancel")],
    ])
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


def images_done_keyboard(prefix: str, count: int, allow_keep: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if count > 0:
        rows.append([InlineKeyboardButton(text=f"✅ Готово ({count}/6)", callback_data=f"{prefix}:done")])
    if allow_keep:
        rows.append([InlineKeyboardButton(text="➖ Оставить прежние фото", callback_data=f"{prefix}:keep")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def extract_image(message: Message):
    if message.photo:
        return {"file_id": message.photo[-1].file_id, "image_type": "photo"}
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return {"file_id": message.document.file_id, "image_type": "document"}
    return None


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
    db = await connect()
    try:
        issued = await (await db.execute("SELECT COUNT(*) c FROM assignments WHERE product_id=?", (product_id,))).fetchone()
        favorites = await (await db.execute("SELECT COUNT(*) c FROM favorites WHERE product_id=?", (product_id,))).fetchone()
    finally:
        await db.close()
    image_count = await send_product_gallery(callback.bot, callback.from_user.id, product_id)
    created_text = __import__("datetime").datetime.fromtimestamp(product["created_at"], settings.timezone).strftime("%d.%m.%Y")
    card_text = (
        f"📦 <b>Товар #{product['id']}</b>\n\n"
        f"Категория: <b>{product['category']}</b>\n"
        f"Название: <b>{product['title']}</b>\n"
        f"Описание: {product['description']}\n"
        f"Цена: <b>{product['price_text']}</b>\n"
        f"Фотографий: <b>{image_count}</b>\n"
        f"📦 Выдан: <b>{issued['c']}</b> раз\n"
        f"⭐ В избранном: <b>{favorites['c']}</b>\n"
        f"📅 Добавлен: <b>{created_text}</b>\n"
        f"Статус: <b>{'В корзине' if deleted else ('Активен' if product['active'] else 'Скрыт')}</b>"
    )

    await callback.message.answer(
        card_text,
        parse_mode="HTML",
        reply_markup=product_manage_keyboard(product["id"], product["active"], deleted),
    )


@router.message(Command("admin"))
async def admin(message: Message) -> None:
    if admin_only(message.from_user.id):
        await message.answer(
            brand_header("ПАНЕЛЬ АДМИНИСТРАТОРА"),
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )


@router.callback_query(F.data == "admin:panel")
async def admin_panel_callback(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        brand_header("ПАНЕЛЬ АДМИНИСТРАТОРА"),
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "admin:settings")
async def admin_settings(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "⚙️ <b>Настройки</b>\n\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=admin_settings_keyboard(),
    )


@router.callback_query(F.data == "admin:welcome_text")
async def welcome_text_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    current = await get_bot_setting("welcome_text", "Используется стандартный текст.")
    await state.set_state(EditWelcome.text)
    await callback.answer()
    await callback.message.answer(
        "✏️ <b>Изменение приветствия</b>\n\n"
        "Текущий текст:\n\n" + current +
        "\n\nОтправьте новое приветственное сообщение одним сообщением. "
        "Оно будет показываться под баннером при /start.\n\n"
        "Для отмены используйте /cancel.",
        parse_mode="HTML",
    )


@router.message(EditWelcome.text)
async def welcome_text_save(message: Message, state: FSMContext) -> None:
    if not admin_only(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer("Текст слишком короткий. Отправьте приветствие длиной от 20 символов.")
        return
    if len(text) > 3500:
        await message.answer("Текст слишком длинный. Максимум — 3500 символов.")
        return
    await set_bot_setting("welcome_text", text)
    await state.clear()
    await message.answer(
        "✅ Приветственный текст сохранён. Проверьте его командой /start.",
        reply_markup=admin_settings_keyboard(),
    )


@router.callback_query(F.data == "admin:payment_status")
async def payment_status_callback(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    crypto = "✅ включён" if settings.crypto_pay_enabled else "❌ выключен"
    xrocket = "✅ включён" if settings.xrocket_enabled else "❌ выключен"
    await callback.answer()
    await callback.message.answer(
        "💳 <b>Настройки оплаты</b>\n\n"
        f"Crypto Bot: <b>{crypto}</b>\n"
        f"xRocket: <b>{xrocket}</b>\n"
        f"Цена: <b>{settings.price_usdt} USDT</b>\n"
        f"Срок доступа: <b>{settings.access_days} дней</b>\n"
        f"Товаров в день: <b>{settings.products_per_day}</b>\n\n"
        "Цена и срок сейчас меняются в Railway Variables.",
        parse_mode="HTML",
        reply_markup=admin_settings_keyboard(),
    )


@router.callback_query(F.data == "admin:admins")
async def admins_list(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    db = await connect()
    try:
        rows = await (await db.execute(
            "SELECT user_id, added_by, created_at FROM bot_admins ORDER BY created_at DESC"
        )).fetchall()
    finally:
        await db.close()

    protected = sorted(settings.admin_ids)
    protected_text = "\n".join(f"• <code>{uid}</code> — главный" for uid in protected) or "• не заданы"
    added_text = "\n".join(f"• <code>{row['user_id']}</code>" for row in rows) or "• пока нет"
    await callback.answer()
    await callback.message.answer(
        "👤 <b>Администраторы</b>\n\n"
        "<b>Главные из Railway:</b>\n" + protected_text +
        "\n\n<b>Добавленные через бота:</b>\n" + added_text +
        "\n\nГлавных администраторов удалить через бота нельзя.",
        parse_mode="HTML",
        reply_markup=admins_keyboard(rows),
    )


@router.callback_query(F.data == "admin:add_admin")
async def add_admin_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AddAdmin.user_id)
    await callback.answer()
    await callback.message.answer(
        "Отправьте числовой Telegram ID нового администратора.\n\n"
        "Для отмены: /cancel"
    )


@router.message(AddAdmin.user_id)
async def add_admin_save(message: Message, state: FSMContext) -> None:
    if not admin_only(message.from_user.id):
        await state.clear()
        return
    value = (message.text or "").strip()
    if not value.isdigit():
        await message.answer("Нужен числовой Telegram ID, например: 123456789")
        return
    new_admin_id = int(value)
    if new_admin_id in settings.admin_ids:
        await state.clear()
        await message.answer("Этот ID уже является главным администратором.", reply_markup=admin_menu())
        return
    db = await connect()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO bot_admins(user_id, added_by, created_at) VALUES (?, ?, ?)",
            (new_admin_id, message.from_user.id, now_ts()),
        )
        await db.commit()
    finally:
        await db.close()
    await state.clear()
    await message.answer(
        f"✅ Администратор <code>{new_admin_id}</code> добавлен.\n"
        "Он может открыть панель командой /admin.",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data.startswith("admin:remove_admin_confirm:"))
async def remove_admin_confirm(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    if user_id in settings.admin_ids:
        await callback.answer("Главного администратора удалить нельзя.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"Удалить администратора <code>{user_id}</code>?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin:remove_admin:{user_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:admins")],
        ]),
    )


@router.callback_query(F.data.startswith("admin:remove_admin:"))
async def remove_admin(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    if user_id in settings.admin_ids:
        await callback.answer("Главного администратора удалить нельзя.", show_alert=True)
        return
    if user_id == callback.from_user.id:
        await callback.answer("Нельзя удалить самого себя.", show_alert=True)
        return
    db = await connect()
    try:
        await db.execute("DELETE FROM bot_admins WHERE user_id=?", (user_id,))
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Администратор удалён.", show_alert=True)
    await callback.message.answer(
        f"✅ Администратор <code>{user_id}</code> удалён.",
        parse_mode="HTML",
        reply_markup=admin_settings_keyboard(),
    )


@router.callback_query(F.data == "admin:add")
async def add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AddProduct.category)
    await callback.message.answer(
        "Выберите ценовую категорию товара:",
        reply_markup=category_keyboard("admin:addcat"),
    )


@router.callback_query(AddProduct.category, F.data.startswith("admin:addcat:"))
async def add_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_key = callback.data.split(":", 2)[2]
    category = PRODUCT_CATEGORIES.get(category_key)
    if not category:
        await callback.answer("Неизвестная категория.", show_alert=True)
        return
    await state.update_data(category=category)
    await state.set_state(AddProduct.title)
    await callback.answer()
    await callback.message.answer(f"Категория: <b>{category}</b>\n\nНазвание товара:", parse_mode="HTML")


@router.message(AddProduct.category)
async def add_category_text_disabled(message: Message) -> None:
    await message.answer("Выберите категорию кнопкой выше.")


@router.message(AddProduct.title)
async def add_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое. Введите минимум 2 символа.")
        return
    await state.update_data(title=title)
    similar = await find_similar_products(title)
    if similar:
        await state.set_state(DuplicateReview.decision)
        text = "⚠️ <b>В базе найдены похожие товары:</b>\n\n" + "\n".join(
            f"• #{row['id']} {row['title']} — {row['category']}" for row in similar
        ) + "\n\nПродолжить добавление?"
        await message.answer(text, parse_mode="HTML", reply_markup=duplicate_review_keyboard(similar))
        return
    await state.set_state(AddProduct.description)
    await message.answer("Описание товара:")


@router.callback_query(DuplicateReview.decision, F.data == "admin:duplicate_continue")
async def duplicate_continue(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddProduct.description)
    await callback.answer()
    await callback.message.answer("Описание товара:")


@router.callback_query(DuplicateReview.decision, F.data == "admin:duplicate_cancel")
async def duplicate_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Добавление отменено.")
    await callback.message.answer("❌ Добавление товара отменено.", reply_markup=admin_menu())


@router.callback_query(DuplicateReview.decision, F.data.startswith("admin:duplicate_open:"))
async def duplicate_open(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_product_card(callback, int(callback.data.rsplit(":", 1)[1]))


@router.message(AddProduct.description)
async def add_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=(message.text or "").strip(), images=[])
    await state.set_state(AddProduct.image)
    await message.answer(
        "📸 Отправьте от 1 до 6 фотографий товара по одной или сразу альбомом.\n\n"
        "Когда закончите, нажмите «✅ Готово». Лучше отправлять именно как фото, "
        "чтобы они отображались полноценным альбомом."
    )


@router.message(AddProduct.image, F.photo | F.document)
async def add_image(message: Message, state: FSMContext) -> None:
    image = extract_image(message)
    if not image:
        await message.answer("Нужно отправить изображение как фото или графический файл.")
        return
    data = await state.get_data()
    images = list(data.get("images", []))
    if len(images) >= 6:
        await message.answer("Уже загружено максимум 6 фотографий. Нажмите «✅ Готово».", reply_markup=images_done_keyboard("admin:addimages", 6))
        return
    images.append(image)
    await state.update_data(images=images)
    await message.answer(
        f"✅ Фотография добавлена: {len(images)}/6",
        reply_markup=images_done_keyboard("admin:addimages", len(images)),
    )


@router.callback_query(AddProduct.image, F.data == "admin:addimages:done")
async def add_images_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    images = data.get("images", [])
    if not images:
        await callback.answer("Добавьте хотя бы одну фотографию.", show_alert=True)
        return
    await state.set_state(AddProduct.price)
    await callback.answer()
    await callback.message.answer("Цена товара:")


@router.message(AddProduct.image)
async def image_required(message: Message) -> None:
    await message.answer("Отправьте от 1 до 6 изображений, затем нажмите «✅ Готово».")


@router.message(AddProduct.price)
async def add_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    price = (message.text or "").strip()
    images = data.get("images", [])
    if not images:
        await state.set_state(AddProduct.image)
        await message.answer("Сначала добавьте хотя бы одну фотографию.")
        return
    db = await connect()
    try:
        cursor = await db.execute("""
            INSERT INTO products(category, title, description, image_file_id,
                                 image_type, price_text, price_num, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (
            data["category"], data["title"], data["description"],
            images[0]["file_id"], images[0]["image_type"],
            price, parse_price(price), now_ts(),
        ))
        product_id = cursor.lastrowid
        await db.commit()
    finally:
        await db.close()
    await save_product_images(product_id, images, replace=True)
    await state.clear()
    await message.answer(
        f"✅ Товар добавлен. Фотографий: {len(images)}.",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "admin:products_hub")
async def products_hub(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    await callback.answer()
    await callback.message.answer("📦 <b>Управление товарами</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=products_hub_keyboard())


@router.callback_query(F.data == "admin:product_search")
async def product_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        return
    await state.set_state(ProductSearch.query)
    await callback.answer()
    await callback.message.answer("🔍 Введите название товара или часть названия.\n\nНапример: <code>Apple TV</code>", parse_mode="HTML")


@router.message(ProductSearch.query)
async def product_search_result(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите минимум 2 символа.")
        return
    rows = await search_products(query, limit=20)
    await state.clear()
    if not rows:
        await message.answer(f"🔍 По запросу <b>{query}</b> ничего не найдено.", parse_mode="HTML", reply_markup=products_hub_keyboard())
        return
    await message.answer(
        f"🔍 <b>Результаты поиска</b>\nЗапрос: <b>{query}</b>\nНайдено: <b>{len(rows)}</b>",
        parse_mode="HTML",
        reply_markup=products_list_keyboard(rows, 0),
    )


@router.callback_query(F.data.startswith("admin:product_filter:"))
async def product_filter(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    parts = callback.data.split(":")
    filter_key = parts[2]
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    where = "deleted_at IS NULL"
    params = []
    labels = {"active": "Активные", "hidden": "Скрытые"}
    if filter_key in PRODUCT_CATEGORIES:
        where += " AND category=?"
        params.append(PRODUCT_CATEGORIES[filter_key])
        label = PRODUCT_CATEGORIES[filter_key]
    elif filter_key == "active":
        where += " AND active=1"
        label = labels[filter_key]
    elif filter_key == "hidden":
        where += " AND active=0"
        label = labels[filter_key]
    else:
        await callback.answer("Неизвестный фильтр.", show_alert=True)
        return
    params.append(page * 10)
    db = await connect()
    try:
        rows = await (await db.execute(f"SELECT * FROM products WHERE {where} ORDER BY id DESC LIMIT 10 OFFSET ?", tuple(params))).fetchall()
    finally:
        await db.close()
    await callback.answer()
    if not rows:
        await callback.message.answer(f"📂 В фильтре <b>{label}</b> товаров нет.", parse_mode="HTML", reply_markup=products_hub_keyboard())
        return
    await callback.message.answer(f"📂 <b>{label}</b>", parse_mode="HTML", reply_markup=products_list_keyboard(rows, page))


@router.callback_query(F.data == "admin:popular_products")
async def popular_products(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        return
    db = await connect()
    try:
        rows = await (await db.execute("""
            SELECT p.*, COUNT(DISTINCT a.user_id) issue_count, COUNT(DISTINCT f.user_id) favorite_count
            FROM products p
            LEFT JOIN assignments a ON a.product_id=p.id
            LEFT JOIN favorites f ON f.product_id=p.id
            WHERE p.deleted_at IS NULL
            GROUP BY p.id
            ORDER BY issue_count DESC, favorite_count DESC, p.id DESC
            LIMIT 10
        """)).fetchall()
    finally:
        await db.close()
    await callback.answer()
    if not rows:
        await callback.message.answer("📈 Пока нет товаров для статистики.", reply_markup=products_hub_keyboard())
        return
    text = "📈 <b>Популярные товары</b>\n\n" + "\n".join(
        f"{i}. #{row['id']} {row['title']} — 📦 {row['issue_count']} / ⭐ {row['favorite_count']}"
        for i, row in enumerate(rows, 1)
    )
    buttons = [[InlineKeyboardButton(text=f"👁 #{row['id']} {row['title'][:28]}", callback_data=f"admin:product:{row['id']}")] for row in rows]
    buttons.append([InlineKeyboardButton(text="⬅️ Управление товарами", callback_data="admin:products_hub")])
    await callback.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


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
    )
    await callback.answer()
    await callback.message.answer(
        f"✏️ Редактирование товара #{product_id}\n\n"
        f"Текущая категория: <b>{product['category']}</b>\n\n"
        "Выберите новую категорию или оставьте прежнюю:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            *category_keyboard(f"admin:editcat:{product_id}").inline_keyboard,
            [InlineKeyboardButton(text="➖ Оставить прежнюю", callback_data=f"admin:editcat:{product_id}:keep")],
        ]),
    )


@router.callback_query(EditProduct.category, F.data.startswith("admin:editcat:"))
async def edit_category(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    value = parts[-1]
    category = None if value == "keep" else PRODUCT_CATEGORIES.get(value)
    if value != "keep" and not category:
        await callback.answer("Неизвестная категория.", show_alert=True)
        return
    await state.update_data(category=category)
    await state.set_state(EditProduct.title)
    await callback.answer()
    await callback.message.answer("Введите новое название или «-», чтобы оставить прежнее.")


@router.message(EditProduct.category)
async def edit_category_text_disabled(message: Message) -> None:
    await message.answer("Выберите категорию кнопкой выше.")


@router.message(EditProduct.title)
async def edit_title(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    await state.update_data(title=None if value == "-" else value)
    await state.set_state(EditProduct.description)
    await message.answer("Введите новое описание или «-», чтобы оставить прежнее.")


@router.message(EditProduct.description)
async def edit_description(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    await state.update_data(description=None if value == "-" else value, images=[])
    await state.set_state(EditProduct.image)
    await message.answer(
        "📸 Отправьте от 1 до 6 новых фотографий, чтобы полностью заменить старые.\n"
        "Либо нажмите «➖ Оставить прежние фото». После загрузки нажмите «✅ Готово».",
        reply_markup=images_done_keyboard("admin:editimages", 0, allow_keep=True),
    )


@router.message(EditProduct.image, F.photo | F.document)
async def edit_image(message: Message, state: FSMContext) -> None:
    image = extract_image(message)
    if not image:
        await message.answer("Нужно отправить изображение.")
        return
    data = await state.get_data()
    images = list(data.get("images", []))
    if len(images) >= 6:
        await message.answer("Уже загружено максимум 6 фотографий.", reply_markup=images_done_keyboard("admin:editimages", 6, allow_keep=True))
        return
    images.append(image)
    await state.update_data(images=images, replace_images=True)
    await message.answer(
        f"✅ Новая фотография добавлена: {len(images)}/6",
        reply_markup=images_done_keyboard("admin:editimages", len(images), allow_keep=True),
    )


@router.callback_query(EditProduct.image, F.data == "admin:editimages:done")
async def edit_images_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("images"):
        await callback.answer("Добавьте хотя бы одну новую фотографию или оставьте прежние.", show_alert=True)
        return
    await state.update_data(replace_images=True)
    await state.set_state(EditProduct.price)
    await callback.answer()
    await callback.message.answer("Введите новую цену или «-», чтобы оставить прежнюю.")


@router.callback_query(EditProduct.image, F.data == "admin:editimages:keep")
async def edit_keep_images(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(images=[], replace_images=False)
    await state.set_state(EditProduct.price)
    await callback.answer()
    await callback.message.answer("Введите новую цену или «-», чтобы оставить прежнюю.")


@router.message(EditProduct.image)
async def edit_image_invalid(message: Message) -> None:
    await message.answer("Отправьте фотографии или воспользуйтесь кнопкой «Оставить прежние фото».")


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
    price_text = product["price_text"] if value == "-" else value
    price_num = product["price_num"] if value == "-" else parse_price(value)
    images = data.get("images", [])
    replace_images = bool(data.get("replace_images"))
    first_image = images[0] if replace_images and images else None

    db = await connect()
    try:
        if first_image:
            await db.execute("""
                UPDATE products
                SET category=?, title=?, description=?, image_file_id=?,
                    image_type=?, price_text=?, price_num=?
                WHERE id=? AND deleted_at IS NULL
            """, (
                category, title, description, first_image["file_id"],
                first_image["image_type"], price_text, price_num, product_id,
            ))
        else:
            await db.execute("""
                UPDATE products
                SET category=?, title=?, description=?, price_text=?, price_num=?
                WHERE id=? AND deleted_at IS NULL
            """, (category, title, description, price_text, price_num, product_id))
        await db.commit()
    finally:
        await db.close()

    if replace_images:
        await save_product_images(product_id, images, replace=True)

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
        await callback.answer("Нет доступа.", show_alert=True)
        return
    today_start = int(__import__("datetime").datetime.combine(
        __import__("datetime").datetime.now(settings.timezone).date(),
        __import__("datetime").time.min,
        tzinfo=settings.timezone,
    ).timestamp())
    db = await connect()
    try:
        total = await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone()
        active = await (await db.execute(
            "SELECT COUNT(*) c FROM users WHERE access_until>?", (now_ts(),)
        )).fetchone()
        new_today = await (await db.execute(
            "SELECT COUNT(*) c FROM users WHERE created_at>=?", (today_start,)
        )).fetchone()
        seen_today = await (await db.execute(
            "SELECT COUNT(*) c FROM users WHERE last_seen>=?", (today_start,)
        )).fetchone()
        blocked = await (await db.execute(
            "SELECT COUNT(*) c FROM users WHERE blocked=1"
        )).fetchone()
    finally:
        await db.close()
    await callback.answer()
    await callback.message.answer(
        brand_header("ПОЛЬЗОВАТЕЛИ") +
        f"\n👥 Всего: <b>{total['c']}</b>\n"
        f"💎 Активная подписка: <b>{active['c']}</b>\n"
        f"🆕 Новых сегодня: <b>{new_today['c']}</b>\n"
        f"🟢 Заходили сегодня: <b>{seen_today['c']}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked['c']}</b>",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "admin:stats")
async def stats(callback: CallbackQuery) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    local_now = __import__("datetime").datetime.now(settings.timezone)
    today = local_now.date().isoformat()
    today_start = int(__import__("datetime").datetime.combine(
        local_now.date(), __import__("datetime").time.min, tzinfo=settings.timezone
    ).timestamp())
    db = await connect()
    try:
        users = await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone()
        active_users = await (await db.execute("SELECT COUNT(*) c FROM users WHERE access_until>?", (now_ts(),))).fetchone()
        products = await (await db.execute("SELECT COUNT(*) c FROM products WHERE active=1 AND deleted_at IS NULL")).fetchone()
        assignments = await (await db.execute("SELECT COUNT(*) c FROM assignments")).fetchone()
        today_assignments = await (await db.execute("SELECT COUNT(*) c FROM assignments WHERE assignment_date=?", (today,))).fetchone()
        sold = await (await db.execute("SELECT COUNT(*) c FROM feedback WHERE status='sold'")).fetchone()
        paid_today = await (await db.execute("SELECT COUNT(*) c FROM invoices WHERE activated=1 AND paid_at>=?", (today_start,))).fetchone()
        revenue = await (await db.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) total FROM invoices WHERE activated=1")).fetchone()
        categories = await (await db.execute("SELECT p.category, COUNT(a.product_id) c FROM assignments a JOIN products p ON p.id=a.product_id GROUP BY p.category ORDER BY c DESC")).fetchall()
        last_broadcast = await (await db.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 1")).fetchone()
    finally:
        await db.close()
    category_text = "\n".join(f"• {row['category']}: <b>{row['c']}</b>" for row in categories) or "• Пока нет выдач"
    broadcast_text = "Нет" if not last_broadcast else f"{last_broadcast['sent_count']} доставлено / {last_broadcast['failed_count']} ошибок"
    await callback.answer()
    await callback.message.answer(
        brand_header("АНАЛИТИКА 3.0") +
        f"\n👥 Пользователей: <b>{users['c']}</b>\n"
        f"💎 Активных подписок: <b>{active_users['c']}</b>\n"
        f"📦 Активных товаров: <b>{products['c']}</b>\n"
        f"🎁 Всего выдач: <b>{assignments['c']}</b>\n"
        f"☀️ Выдано сегодня: <b>{today_assignments['c']}</b>\n"
        f"✅ Отмечено проданными: <b>{sold['c']}</b>\n"
        f"💳 Оплат сегодня: <b>{paid_today['c']}</b>\n"
        f"💰 Общая выручка: <b>{revenue['total']} USDT</b>\n\n"
        f"<b>Популярность категорий:</b>\n{category_text}\n\n"
        f"<b>Последняя рассылка:</b> {broadcast_text}",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "admin:send")
async def send_now(callback: CallbackQuery) -> None:
    if admin_only(callback.from_user.id):
        await callback.answer()
        await callback.message.answer(
            "Автоматическая выдача отключена. Пользователи сами выбирают категорию "
            "для каждого из двух товаров в день."
        )


@router.message(Command("sendtoday"))
async def send_today(message: Message) -> None:
    if admin_only(message.from_user.id):
        await message.answer(
            "Автоматическая выдача отключена. Пользователи получают товары вручную."
        )


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(Broadcast.message)
    await callback.message.answer(
        "📣 <b>Новая рассылка</b>\n\n"
        "Отправьте одно сообщение: текст, фото, видео, GIF или документ. "
        "Бот сначала покажет предпросмотр и попросит подтверждение.\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML",
    )


@router.message(Broadcast.message)
async def broadcast_preview(message: Message, state: FSMContext) -> None:
    if not admin_only(message.from_user.id):
        await state.clear()
        return
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
    await state.set_state(Broadcast.confirm)
    await message.answer(
        "👁 <b>Предпросмотр рассылки</b>",
        parse_mode="HTML",
    )
    await message.bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    await message.answer(
        "Отправить это сообщение всем пользователям?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить всем", callback_data="admin:broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:broadcast_cancel")],
        ]),
    )


@router.callback_query(Broadcast.confirm, F.data == "admin:broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Рассылка отменена.", show_alert=True)
    await callback.message.answer("Рассылка отменена.", reply_markup=admin_menu())


@router.callback_query(Broadcast.confirm, F.data == "admin:broadcast_confirm")
async def broadcast_send(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(callback.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    if not source_chat_id or not source_message_id:
        await state.clear()
        await callback.answer("Не найдено сообщение для рассылки.", show_alert=True)
        return
    await callback.answer()
    progress = await callback.message.answer("⏳ Рассылка началась…")
    db = await connect()
    try:
        users = await (await db.execute("SELECT user_id FROM users WHERE blocked=0")).fetchall()
    finally:
        await db.close()
    sent = 0
    failed = 0
    for index, user in enumerate(users, start=1):
        try:
            await callback.bot.copy_message(user["user_id"], source_chat_id, source_message_id)
            sent += 1
        except Exception:
            failed += 1
        if index % 50 == 0:
            try:
                await progress.edit_text(f"⏳ Обработано: {index}/{len(users)}\n✅ {sent}  ❌ {failed}")
            except Exception:
                pass
    db = await connect()
    try:
        await db.execute(
            "INSERT INTO broadcasts(admin_id, sent_count, failed_count, created_at) VALUES (?, ?, ?, ?)",
            (callback.from_user.id, sent, failed, now_ts()),
        )
        if failed:
            # Не считаем всех ошибочных навсегда заблокировавшими, но отмечаем тех, кому доставка не удалась.
            pass
        await db.commit()
    finally:
        await db.close()
    await state.clear()
    await progress.edit_text(
        "✅ <b>Рассылка завершена</b>\n\n"
        f"Доставлено: <b>{sent}</b>\n"
        f"Не доставлено: <b>{failed}</b>",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


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
