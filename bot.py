import asyncio
import html
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "").strip()
CRYPTO_PAY_NETWORK = os.getenv("CRYPTO_PAY_NETWORK", "testnet").lower()

PRICE_USDT = os.getenv("PRICE_USDT", "20")
ACCESS_DAYS = int(os.getenv("ACCESS_DAYS", "5"))
PRODUCTS_PER_DAY = int(os.getenv("PRODUCTS_PER_DAY", "2"))

TIMEZONE_NAME = os.getenv("TIMEZONE", "Europe/Rome")
SEND_HOUR = int(os.getenv("SEND_HOUR", "10"))
SEND_MINUTE = int(os.getenv("SEND_MINUTE", "0"))
PAYMENT_CHECK_SECONDS = max(15, int(os.getenv("PAYMENT_CHECK_SECONDS", "30")))

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/app/data/dt_team.db"))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "")
BRAND_NAME = os.getenv("BRAND_NAME", "DT Team")
REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "1"))

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")
if not CRYPTO_PAY_TOKEN:
    raise RuntimeError("Не задан CRYPTO_PAY_TOKEN")

TZ = ZoneInfo(TIMEZONE_NAME)
CRYPTO_BASE = (
    "https://testnet-pay.crypt.bot/api"
    if CRYPTO_PAY_NETWORK == "testnet"
    else "https://pay.crypt.bot/api"
)

bot = Bot(BOT_TOKEN)
router = Router()
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
scheduler = AsyncIOScheduler(timezone=TZ)


class AddDay(StatesGroup):
    date = State()
    slot = State()
    title = State()
    description = State()
    buy_price = State()
    sell_price = State()
    marketplaces = State()
    image = State()


class PromoInput(StatesGroup):
    code = State()


class BroadcastInput(StatesGroup):
    content = State()


def ts_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def dt_header(title: str) -> str:
    return (
        "⚡️ <b>DT TEAM</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>{html.escape(title)}</b>\n"
    )


def client_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔥 Товары сегодня")],
            [KeyboardButton(text="💎 Купить доступ"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="📚 Мой архив"), KeyboardButton(text="🎁 Пригласить друга")],
            [KeyboardButton(text="🎟 Промокод"), KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел DT Team",
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товары", callback_data="adm:add")],
        [InlineKeyboardButton(text="🚀 Разослать сегодня", callback_data="adm:send")],
        [InlineKeyboardButton(text="📅 Календарь контента", callback_data="adm:calendar")],
        [InlineKeyboardButton(text="📊 Аналитика", callback_data="adm:stats")],
        [InlineKeyboardButton(text="📣 Общая рассылка", callback_data="adm:broadcast")],
    ])


def buy_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💎 Оплатить {PRICE_USDT} USDT",
            callback_data="payment:create"
        )],
        [InlineKeyboardButton(text="👤 Проверить доступ", callback_data="profile:show")],
    ])


async def init_db() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            access_started INTEGER NOT NULL DEFAULT 0,
            access_until INTEGER NOT NULL DEFAULT 0,
            referrer_id INTEGER,
            referral_rewarded INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            expiry_notice_sent INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publish_date TEXT NOT NULL,
            slot INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            buy_price TEXT NOT NULL,
            sell_price TEXT NOT NULL,
            marketplaces TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_type TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(publish_date, slot)
        );

        CREATE TABLE IF NOT EXISTS deliveries (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            delivered_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id)
        );

        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            amount TEXT NOT NULL,
            asset TEXT NOT NULL,
            pay_url TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            paid_at INTEGER,
            activated INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            days INTEGER NOT NULL,
            max_uses INTEGER NOT NULL,
            used_count INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS promo_uses (
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            used_at INTEGER NOT NULL,
            PRIMARY KEY(code, user_id)
        );
        """)
        await db.commit()


async def crypto_call(method: str, data: dict[str, Any] | None = None) -> Any:
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.post(f"{CRYPTO_BASE}/{method}", data=data or {}) as response:
            payload = await response.json(content_type=None)
            if response.status != 200 or not payload.get("ok"):
                raise RuntimeError(f"Crypto Pay error: {payload}")
            return payload["result"]


async def register_user(message: Message, referrer_id: int | None = None) -> None:
    user = message.from_user
    if not user:
        return
    if referrer_id == user.id:
        referrer_id = None

    async with aiosqlite.connect(DATABASE_PATH) as db:
        exists = await (await db.execute(
            "SELECT user_id FROM users WHERE user_id=?", (user.id,)
        )).fetchone()

        if exists:
            await db.execute("""
                UPDATE users SET username=?, full_name=?, last_seen=?, blocked=0
                WHERE user_id=?
            """, (user.username or "", user.full_name or "", ts_now(), user.id))
        else:
            await db.execute("""
                INSERT INTO users(
                    user_id, username, full_name, created_at, last_seen, referrer_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user.id, user.username or "", user.full_name or "",
                ts_now(), ts_now(), referrer_id
            ))
        await db.commit()


async def access_data(user_id: int) -> tuple[int, int]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        row = await (await db.execute("""
            SELECT access_started, access_until FROM users WHERE user_id=?
        """, (user_id,))).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


async def has_access(user_id: int) -> bool:
    _, until = await access_data(user_id)
    return until > ts_now()


async def activate_access(user_id: int, days: int) -> int:
    started, until = await access_data(user_id)
    current = ts_now()
    base = max(current, until)
    new_started = started if until > current and started else current
    new_until = base + days * 86400

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO users(user_id, created_at, last_seen, access_started, access_until)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_started=?,
                access_until=?,
                expiry_notice_sent=0
        """, (
            user_id, current, current, new_started, new_until,
            new_started, new_until
        ))
        await db.commit()
    return new_until


async def reward_referrer(user_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        row = await (await db.execute("""
            SELECT referrer_id, referral_rewarded
            FROM users WHERE user_id=?
        """, (user_id,))).fetchone()
        if not row or not row[0] or row[1]:
            return
        referrer_id = int(row[0])
        await db.execute(
            "UPDATE users SET referral_rewarded=1 WHERE user_id=?", (user_id,)
        )
        await db.commit()

    until = await activate_access(referrer_id, REFERRAL_BONUS_DAYS)
    try:
        await bot.send_message(
            referrer_id,
            dt_header("РЕФЕРАЛЬНЫЙ БОНУС") +
            f"\n🎉 Ваш друг оплатил доступ.\n"
            f"Вам начислено: <b>{REFERRAL_BONUS_DAYS} день</b>.\n"
            f"Новый срок доступа: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def products_for_date(date_key: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        return await (await db.execute("""
            SELECT * FROM products WHERE publish_date=? ORDER BY slot
        """, (date_key,))).fetchall()


def product_caption(product) -> str:
    return (
        f"⚡️ <b>DT TEAM — ТОВАР #{product['slot']}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>{html.escape(product['title'])}</b>\n\n"
        f"{html.escape(product['description'])}\n\n"
        f"💰 <b>Закупка:</b> {html.escape(product['buy_price'])}\n"
        f"🏷 <b>Продажа:</b> {html.escape(product['sell_price'])}\n"
        f"🛒 <b>Где продавать:</b> {html.escape(product['marketplaces'])}\n\n"
        "🚀 <i>Материал подготовлен DT Team</i>"
    )


async def send_product(user_id: int, product, force: bool = False) -> bool:
    if not force:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            exists = await (await db.execute("""
                SELECT 1 FROM deliveries WHERE user_id=? AND product_id=?
            """, (user_id, product["id"]))).fetchone()
            if exists:
                return False

    try:
        caption = product_caption(product)
        if product["file_type"] == "document":
            await bot.send_document(
                user_id, product["file_id"], caption=caption, parse_mode="HTML"
            )
        else:
            await bot.send_photo(
                user_id, product["file_id"], caption=caption, parse_mode="HTML"
            )

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("""
                INSERT OR IGNORE INTO deliveries(user_id, product_id, delivered_at)
                VALUES (?, ?, ?)
            """, (user_id, product["id"], ts_now()))
            await db.commit()
        return True
    except Exception as exc:
        logging.warning("Не удалось отправить товар user=%s: %s", user_id, exc)
        return False


async def send_today_to_user(user_id: int, force: bool = False) -> int:
    if not await has_access(user_id):
        return 0
    products = await products_for_date(datetime.now(TZ).date().isoformat())
    sent = 0
    for product in products:
        if await send_product(user_id, product, force):
            sent += 1
        await asyncio.sleep(0.08)
    return sent


async def daily_broadcast() -> None:
    date_key = datetime.now(TZ).date().isoformat()
    products = await products_for_date(date_key)
    if len(products) != PRODUCTS_PER_DAY:
        logging.warning("На %s загружено %s/%s товаров", date_key, len(products), PRODUCTS_PER_DAY)
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        users = await (await db.execute("""
            SELECT user_id FROM users
            WHERE access_until>? AND blocked=0
        """, (ts_now(),))).fetchall()

    total = 0
    for (user_id,) in users:
        total += await send_today_to_user(int(user_id))
    logging.info("Отправлено материалов: %s", total)


async def check_invoices() -> None:
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            rows = await (await db.execute("""
                SELECT invoice_id FROM invoices
                WHERE status='active'
                ORDER BY created_at DESC LIMIT 100
            """)).fetchall()

        if not rows:
            return

        result = await crypto_call(
            "getInvoices",
            {"invoice_ids": ",".join(str(row[0]) for row in rows)}
        )

        for invoice in result.get("items", []):
            if invoice.get("status") != "paid":
                continue

            invoice_id = int(invoice["invoice_id"])
            async with aiosqlite.connect(DATABASE_PATH) as db:
                db.row_factory = aiosqlite.Row
                stored = await (await db.execute("""
                    SELECT * FROM invoices WHERE invoice_id=?
                """, (invoice_id,))).fetchone()

                if not stored or stored["activated"]:
                    continue

                await db.execute("""
                    UPDATE invoices
                    SET status='paid', paid_at=?, activated=1
                    WHERE invoice_id=?
                """, (ts_now(), invoice_id))
                await db.commit()

            until = await activate_access(stored["user_id"], ACCESS_DAYS)
            await reward_referrer(stored["user_id"])

            await bot.send_message(
                stored["user_id"],
                dt_header("ДОСТУП АКТИВИРОВАН") +
                f"\n✅ Платёж подтверждён.\n"
                f"📅 Срок: <b>{ACCESS_DAYS} дней</b>\n"
                f"🔥 Товаров в день: <b>{PRODUCTS_PER_DAY}</b>\n"
                f"⏳ Доступ до: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>",
                parse_mode="HTML",
                reply_markup=client_menu()
            )
            await send_today_to_user(stored["user_id"])
    except Exception:
        logging.exception("Ошибка проверки Crypto Pay")


async def expiry_notices() -> None:
    current = ts_now()
    threshold = current + 86400
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await (await db.execute("""
            SELECT user_id, access_until FROM users
            WHERE access_until>? AND access_until<=?
              AND expiry_notice_sent=0 AND blocked=0
        """, (current, threshold))).fetchall()

    for user_id, until in rows:
        try:
            await bot.send_message(
                user_id,
                dt_header("ДОСТУП СКОРО ЗАКОНЧИТСЯ") +
                f"\n⏳ До: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>\n\n"
                "Продлите доступ, чтобы не пропустить следующие товары.",
                parse_mode="HTML",
                reply_markup=buy_menu()
            )
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("""
                    UPDATE users SET expiry_notice_sent=1 WHERE user_id=?
                """, (user_id,))
                await db.commit()
        except Exception:
            pass


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    referrer_id = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_"):
        raw = parts[1].replace("ref_", "", 1)
        if raw.isdigit():
            referrer_id = int(raw)

    await register_user(message, referrer_id)

    await message.answer(
        dt_header("ТОВАРЫ, КОТОРЫЕ МОЖНО ПРОДАВАТЬ") +
        f"\nКаждый день DT Team выдаёт <b>{PRODUCTS_PER_DAY} готовых товара</b>:\n\n"
        "✅ фото товара\n"
        "✅ готовое описание\n"
        "✅ ориентир закупочной цены\n"
        "✅ рекомендуемая цена продажи\n"
        "✅ площадки для размещения\n\n"
        f"💎 <b>{PRICE_USDT} USDT</b> — доступ на <b>{ACCESS_DAYS} дней</b>\n"
        f"🔥 Всего до <b>{ACCESS_DAYS * PRODUCTS_PER_DAY} товаров</b>",
        parse_mode="HTML",
        reply_markup=client_menu()
    )


@router.message(F.text == "💎 Купить доступ")
async def buy_handler(message: Message) -> None:
    await register_user(message)
    await message.answer(
        dt_header("ТАРИФ DT TEAM") +
        f"\n💎 Стоимость: <b>{PRICE_USDT} USDT</b>\n"
        f"📅 Срок: <b>{ACCESS_DAYS} дней</b>\n"
        f"📦 Ежедневно: <b>{PRODUCTS_PER_DAY} товара</b>\n"
        f"🔥 Всего: до <b>{ACCESS_DAYS * PRODUCTS_PER_DAY} товаров</b>\n\n"
        "После оплаты доступ включится автоматически.",
        parse_mode="HTML",
        reply_markup=buy_menu()
    )


@router.callback_query(F.data == "payment:create")
async def create_payment(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        result = await crypto_call("createInvoice", {
            "asset": "USDT",
            "amount": PRICE_USDT,
            "description": f"DT Team — access for {ACCESS_DAYS} days",
            "payload": f"dt:{callback.from_user.id}:{ts_now()}",
            "expires_in": 3600,
            "allow_comments": "false",
            "allow_anonymous": "false",
        })

        pay_url = (
            result.get("bot_invoice_url")
            or result.get("mini_app_invoice_url")
            or result.get("pay_url")
        )
        invoice_id = int(result["invoice_id"])

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("""
                INSERT OR REPLACE INTO invoices(
                    invoice_id, user_id, status, amount, asset,
                    pay_url, created_at, activated
                ) VALUES (?, ?, 'active', ?, 'USDT', ?, ?, 0)
            """, (
                invoice_id, callback.from_user.id,
                PRICE_USDT, pay_url, ts_now()
            ))
            await db.commit()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Перейти к оплате", url=pay_url)],
            [InlineKeyboardButton(
                text="🔄 Проверить платёж",
                callback_data=f"payment:check:{invoice_id}"
            )]
        ])

        await bot.send_message(
            callback.from_user.id,
            dt_header("СЧЁТ СОЗДАН") +
            f"\nСумма: <b>{PRICE_USDT} USDT</b>\n"
            "Счёт действует 60 минут.\n\n"
            "После оплаты нажмите «Проверить платёж». "
            "Также проверка выполняется автоматически.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception:
        logging.exception("Не удалось создать счёт")
        await bot.send_message(
            callback.from_user.id,
            f"Не удалось создать счёт. Поддержка: {SUPPORT_USERNAME}"
        )


@router.callback_query(F.data.startswith("payment:check:"))
async def manual_payment_check(callback: CallbackQuery) -> None:
    await check_invoices()
    if await has_access(callback.from_user.id):
        await callback.answer("Платёж подтверждён. Доступ активен.", show_alert=True)
    else:
        await callback.answer(
            "Платёж пока не найден. Повторите проверку через несколько секунд.",
            show_alert=True
        )


async def profile_text(user_id: int) -> str:
    started, until = await access_data(user_id)
    active = until > ts_now()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        delivered = (await (await db.execute("""
            SELECT COUNT(*) FROM deliveries WHERE user_id=?
        """, (user_id,))).fetchone())[0]
        referrals = (await (await db.execute("""
            SELECT COUNT(*) FROM users
            WHERE referrer_id=? AND referral_rewarded=1
        """, (user_id,))).fetchone())[0]

    if not active:
        return (
            dt_header("ПРОФИЛЬ") +
            "\n❌ Активного доступа нет.\n"
            f"📦 Получено товаров ранее: <b>{delivered}</b>\n"
            f"🎁 Оплативших друзей: <b>{referrals}</b>"
        )

    return (
        dt_header("ПРОФИЛЬ") +
        "\n✅ Статус: <b>доступ активен</b>\n"
        f"⏳ До: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"📦 Получено товаров: <b>{delivered}</b>\n"
        f"🎁 Оплативших друзей: <b>{referrals}</b>"
    )


@router.message(F.text == "👤 Профиль")
async def profile_handler(message: Message) -> None:
    await register_user(message)
    await message.answer(
        await profile_text(message.from_user.id),
        parse_mode="HTML",
        reply_markup=buy_menu() if not await has_access(message.from_user.id) else None
    )


@router.callback_query(F.data == "profile:show")
async def profile_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await bot.send_message(
        callback.from_user.id,
        await profile_text(callback.from_user.id),
        parse_mode="HTML"
    )


@router.message(F.text == "🔥 Товары сегодня")
async def today_handler(message: Message) -> None:
    await register_user(message)
    if not await has_access(message.from_user.id):
        await message.answer(
            dt_header("НЕТ АКТИВНОГО ДОСТУПА") +
            "\nДля получения товарных подборок оформите доступ.",
            parse_mode="HTML",
            reply_markup=buy_menu()
        )
        return

    products = await products_for_date(datetime.now(TZ).date().isoformat())
    if len(products) != PRODUCTS_PER_DAY:
        await message.answer(
            dt_header("ПОДБОРКА ГОТОВИТСЯ") +
            "\nСегодняшние товары ещё не опубликованы.",
            parse_mode="HTML"
        )
        return

    for product in products:
        await send_product(message.from_user.id, product, force=True)


@router.message(F.text == "📚 Мой архив")
async def archive_handler(message: Message) -> None:
    await register_user(message)
    if not await has_access(message.from_user.id):
        await message.answer("Архив доступен только при активном доступе.", reply_markup=buy_menu())
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT p.* FROM deliveries d
            JOIN products p ON p.id=d.product_id
            WHERE d.user_id=?
            ORDER BY p.publish_date DESC, p.slot
            LIMIT 10
        """, (message.from_user.id,))).fetchall()

    if not rows:
        await message.answer("В архиве пока нет товаров.")
        return

    await message.answer(
        dt_header("ПОСЛЕДНИЕ ТОВАРЫ") +
        "\nПоказываю до 10 последних полученных товаров.",
        parse_mode="HTML"
    )
    for product in rows:
        await send_product(message.from_user.id, product, force=True)


@router.message(F.text == "🎁 Пригласить друга")
async def referral_handler(message: Message) -> None:
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        dt_header("РЕФЕРАЛЬНАЯ ПРОГРАММА") +
        f"\nЗа каждого друга, который оплатит доступ, "
        f"вы получите <b>{REFERRAL_BONUS_DAYS} дополнительный день</b>.\n\n"
        f"Ваша ссылка:\n<code>{link}</code>",
        parse_mode="HTML"
    )


@router.message(F.text == "🎟 Промокод")
async def promo_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(PromoInput.code)
    await message.answer("Введите промокод одним сообщением:")


@router.message(PromoInput.code)
async def promo_use(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    await state.clear()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        promo = await (await db.execute("""
            SELECT * FROM promo_codes WHERE code=? AND active=1
        """, (code,))).fetchone()
        already = await (await db.execute("""
            SELECT 1 FROM promo_uses WHERE code=? AND user_id=?
        """, (code, message.from_user.id))).fetchone()

        if not promo or promo["used_count"] >= promo["max_uses"]:
            await message.answer("Промокод недействителен или закончился.")
            return
        if already:
            await message.answer("Вы уже использовали этот промокод.")
            return

        await db.execute("""
            INSERT INTO promo_uses(code, user_id, used_at) VALUES (?, ?, ?)
        """, (code, message.from_user.id, ts_now()))
        await db.execute("""
            UPDATE promo_codes SET used_count=used_count+1 WHERE code=?
        """, (code,))
        await db.commit()

    until = await activate_access(message.from_user.id, int(promo["days"]))
    await message.answer(
        dt_header("ПРОМОКОД АКТИВИРОВАН") +
        f"\n🎉 Добавлено дней: <b>{promo['days']}</b>\n"
        f"⏳ Доступ до: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>",
        parse_mode="HTML"
    )


@router.message(F.text == "💬 Поддержка")
async def support_handler(message: Message) -> None:
    extra = f"\n📢 Канал: {CHANNEL_USERNAME}" if CHANNEL_USERNAME else ""
    await message.answer(
        dt_header("ПОДДЕРЖКА") +
        f"\nПо вопросам оплаты и доступа:\n{SUPPORT_USERNAME}{extra}",
        parse_mode="HTML"
    )


@router.message(Command("admin"))
async def admin_handler(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer(
            dt_header("ПАНЕЛЬ АДМИНИСТРАТОРА"),
            parse_mode="HTML",
            reply_markup=admin_menu()
        )


@router.callback_query(F.data == "adm:add")
async def admin_add_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AddDay.date)
    await state.update_data(slot=1, products=[])
    await bot.send_message(callback.from_user.id, "Введите дату публикации: ДД.ММ.ГГГГ")


@router.message(Command("addday"))
async def addday_command(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddDay.date)
    await state.update_data(slot=1, products=[])
    await message.answer("Введите дату публикации: ДД.ММ.ГГГГ")


@router.message(AddDay.date)
async def addday_date(message: Message, state: FSMContext) -> None:
    try:
        date_key = datetime.strptime((message.text or "").strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        await message.answer("Неверный формат. Пример: 28.07.2026")
        return
    await state.update_data(date_key=date_key)
    await state.set_state(AddDay.title)
    await message.answer("Товар №1 — название:")


async def text_step(message: Message, state: FSMContext, key: str, next_state: State, prompt: str) -> None:
    if not message.text:
        await message.answer("Нужно отправить текст.")
        return
    await state.update_data(**{key: message.text.strip()})
    await state.set_state(next_state)
    await message.answer(prompt)


@router.message(AddDay.title)
async def add_title(m: Message, s: FSMContext):
    await text_step(m, s, "title", AddDay.description, "Краткое описание:")

@router.message(AddDay.description)
async def add_description(m: Message, s: FSMContext):
    await text_step(m, s, "description", AddDay.buy_price, "Цена закупки:")

@router.message(AddDay.buy_price)
async def add_buy(m: Message, s: FSMContext):
    await text_step(m, s, "buy_price", AddDay.sell_price, "Рекомендуемая цена продажи:")

@router.message(AddDay.sell_price)
async def add_sell(m: Message, s: FSMContext):
    await text_step(m, s, "sell_price", AddDay.marketplaces, "Где продавать / рекомендации:")

@router.message(AddDay.marketplaces)
async def add_marketplaces(m: Message, s: FSMContext):
    await text_step(m, s, "marketplaces", AddDay.image, "Отправьте изображение товара:")


def file_from_message(message: Message):
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return message.document.file_id, "document"
    return None


@router.message(AddDay.image, F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT}))
async def add_image(message: Message, state: FSMContext) -> None:
    file_data = file_from_message(message)
    if not file_data:
        await message.answer("Отправьте изображение.")
        return

    data = await state.get_data()
    products = data.get("products", [])
    products.append({
        "slot": data["slot"],
        "title": data["title"],
        "description": data["description"],
        "buy_price": data["buy_price"],
        "sell_price": data["sell_price"],
        "marketplaces": data["marketplaces"],
        "file_id": file_data[0],
        "file_type": file_data[1],
    })

    if data["slot"] < PRODUCTS_PER_DAY:
        await state.update_data(slot=data["slot"] + 1, products=products)
        for key in ("title", "description", "buy_price", "sell_price", "marketplaces"):
            await state.update_data(**{key: None})
        await state.set_state(AddDay.title)
        await message.answer(f"Товар №{data['slot'] + 1} — название:")
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        for product in products:
            await db.execute("""
                INSERT INTO products(
                    publish_date, slot, title, description, buy_price,
                    sell_price, marketplaces, file_id, file_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(publish_date, slot) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    buy_price=excluded.buy_price,
                    sell_price=excluded.sell_price,
                    marketplaces=excluded.marketplaces,
                    file_id=excluded.file_id,
                    file_type=excluded.file_type
            """, (
                data["date_key"], product["slot"], product["title"],
                product["description"], product["buy_price"],
                product["sell_price"], product["marketplaces"],
                product["file_id"], product["file_type"], ts_now()
            ))
        await db.commit()

    await state.clear()
    await message.answer(
        dt_header("КОНТЕНТ СОХРАНЁН") +
        f"\nНа дату <b>{datetime.fromisoformat(data['date_key']).strftime('%d.%m.%Y')}</b> "
        f"добавлено товаров: <b>{len(products)}</b>",
        parse_mode="HTML",
        reply_markup=admin_menu()
    )


@router.message(AddDay.image)
async def image_wrong(message: Message) -> None:
    await message.answer("Нужно отправить изображение как фото или файл.")


async def stats_text() -> str:
    current = ts_now()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active_users = (await (await db.execute("""
            SELECT COUNT(*) FROM users WHERE access_until>?
        """, (current,))).fetchone())[0]
        paid = (await (await db.execute("""
            SELECT COUNT(*) FROM invoices WHERE status='paid'
        """)).fetchone())[0]
        revenue = (await (await db.execute("""
            SELECT COALESCE(SUM(CAST(amount AS REAL)), 0)
            FROM invoices WHERE status='paid' AND asset='USDT'
        """)).fetchone())[0]
        products = (await (await db.execute("SELECT COUNT(*) FROM products")).fetchone())[0]
        referrals = (await (await db.execute("""
            SELECT COUNT(*) FROM users WHERE referral_rewarded=1
        """)).fetchone())[0]

    return (
        dt_header("АНАЛИТИКА") +
        f"\n👥 Пользователей: <b>{users}</b>\n"
        f"✅ Активный доступ: <b>{active_users}</b>\n"
        f"💳 Оплаченных счетов: <b>{paid}</b>\n"
        f"💰 Выручка: <b>{revenue:g} USDT</b>\n"
        f"📦 Загружено товаров: <b>{products}</b>\n"
        f"🎁 Оплаченных рефералов: <b>{referrals}</b>"
    )


@router.callback_query(F.data == "adm:stats")
async def admin_stats_callback(callback: CallbackQuery) -> None:
    if is_admin(callback.from_user.id):
        await callback.answer()
        await bot.send_message(callback.from_user.id, await stats_text(), parse_mode="HTML")


@router.message(Command("stats"))
async def stats_command(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer(await stats_text(), parse_mode="HTML")


async def calendar_text() -> str:
    today_key = datetime.now(TZ).date().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await (await db.execute("""
            SELECT publish_date, COUNT(*)
            FROM products
            WHERE publish_date>=?
            GROUP BY publish_date
            ORDER BY publish_date
            LIMIT 14
        """, (today_key,))).fetchall()

    if not rows:
        return dt_header("КАЛЕНДАРЬ") + "\nБлижайших материалов нет."

    lines = [dt_header("КАЛЕНДАРЬ КОНТЕНТА")]
    for date_key, count in rows:
        icon = "✅" if count == PRODUCTS_PER_DAY else "⚠️"
        lines.append(
            f"{icon} {datetime.fromisoformat(date_key).strftime('%d.%m.%Y')} — "
            f"<b>{count}/{PRODUCTS_PER_DAY}</b>"
        )
    return "\n".join(lines)


@router.callback_query(F.data == "adm:calendar")
async def calendar_callback(callback: CallbackQuery) -> None:
    if is_admin(callback.from_user.id):
        await callback.answer()
        await bot.send_message(callback.from_user.id, await calendar_text(), parse_mode="HTML")


@router.callback_query(F.data == "adm:send")
async def send_callback(callback: CallbackQuery) -> None:
    if is_admin(callback.from_user.id):
        await callback.answer("Рассылка запущена")
        await daily_broadcast()
        await bot.send_message(callback.from_user.id, "✅ Рассылка завершена.")


@router.message(Command("sendtoday"))
async def sendtoday_command(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer("Рассылка запущена.")
        await daily_broadcast()
        await message.answer("✅ Рассылка завершена.")


@router.callback_query(F.data == "adm:broadcast")
async def broadcast_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(BroadcastInput.content)
    await bot.send_message(
        callback.from_user.id,
        "Отправьте сообщение для общей рассылки. Можно отправить текст, фото или файл."
    )


@router.message(Command("broadcast"))
async def broadcast_command(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        await state.set_state(BroadcastInput.content)
        await message.answer("Отправьте сообщение для общей рассылки.")


@router.message(BroadcastInput.content)
async def broadcast_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        users = await (await db.execute("""
            SELECT user_id FROM users WHERE blocked=0
        """)).fetchall()

    sent = 0
    for (user_id,) in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    await message.answer(f"✅ Рассылка завершена. Доставлено: {sent}.")


@router.message(Command("grant"))
async def grant_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Формат: /grant USER_ID DAYS")
        return
    until = await activate_access(int(parts[1]), int(parts[2]))
    await message.answer(
        f"✅ Доступ выдан до {datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}."
    )


@router.message(Command("promo"))
async def promo_create_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await message.answer("Формат: /promo CODE DAYS USES")
        return

    code = parts[1].upper()
    days = int(parts[2])
    uses = int(parts[3])

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO promo_codes(
                code, days, max_uses, used_count, active, created_at
            ) VALUES (?, ?, ?, 0, 1, ?)
        """, (code, days, uses, ts_now()))
        await db.commit()

    await message.answer(
        f"✅ Промокод <b>{html.escape(code)}</b>: {days} дней, {uses} использований.",
        parse_mode="HTML"
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Операция отменена.")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    await init_db()

    scheduler.add_job(
        daily_broadcast, "cron",
        hour=SEND_HOUR, minute=SEND_MINUTE,
        id="daily_broadcast", replace_existing=True
    )
    scheduler.add_job(
        check_invoices, "interval",
        seconds=PAYMENT_CHECK_SECONDS,
        id="check_invoices", replace_existing=True
    )
    scheduler.add_job(
        expiry_notices, "interval",
        hours=1,
        id="expiry_notices", replace_existing=True
    )
    scheduler.start()

    crypto_app = await crypto_call("getMe")
    logging.info("Crypto Pay connected: %s", crypto_app)
    logging.info(
        "%s запущен. Рассылка %02d:%02d %s",
        BRAND_NAME, SEND_HOUR, SEND_MINUTE, TIMEZONE_NAME
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
