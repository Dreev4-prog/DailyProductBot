import asyncio
import html
import logging
import os
import random
import re
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
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
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
CRYPTO_BASE = "https://testnet-pay.crypt.bot/api" if CRYPTO_PAY_NETWORK == "testnet" else "https://pay.crypt.bot/api"

bot = Bot(BOT_TOKEN)
router = Router()
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
scheduler = AsyncIOScheduler(timezone=TZ)


class AddProduct(StatesGroup):
    category = State()
    title = State()
    description = State()
    image = State()
    price = State()


class PromoInput(StatesGroup):
    code = State()


class BroadcastInput(StatesGroup):
    content = State()


class PreferencesInput(StatesGroup):
    categories = State()
    budget = State()


def ts_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def today_key() -> str:
    return datetime.now(TZ).date().isoformat()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def header(title: str) -> str:
    return f"⚡️ <b>DT TEAM</b>\n━━━━━━━━━━━━━━━━━━\n<b>{html.escape(title)}</b>\n"


def client_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔥 Получить товары")],
            [KeyboardButton(text="💎 Купить доступ"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="📚 Мой архив"), KeyboardButton(text="⚙️ Мои настройки")],
            [KeyboardButton(text="🎁 Пригласить друга"), KeyboardButton(text="🎟 Промокод")],
            [KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел DT Team",
    )


def buy_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💎 Оплатить {PRICE_USDT} USDT", callback_data="payment:create")],
        [InlineKeyboardButton(text="👤 Проверить доступ", callback_data="profile:show")],
    ])


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар в базу", callback_data="adm:add")],
        [InlineKeyboardButton(text="🎲 Раздать товары сейчас", callback_data="adm:send")],
        [InlineKeyboardButton(text="📦 База товаров", callback_data="adm:pool")],
        [InlineKeyboardButton(text="📊 Аналитика", callback_data="adm:stats")],
        [InlineKeyboardButton(text="📣 Общая рассылка", callback_data="adm:broadcast")],
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

        CREATE TABLE IF NOT EXISTS product_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Общее',
            description TEXT NOT NULL,
            buy_price TEXT NOT NULL,
            sell_price TEXT NOT NULL,
            buy_price_num REAL,
            sell_price_num REAL,
            marketplaces TEXT NOT NULL,
            german_title TEXT NOT NULL DEFAULT '',
            german_description TEXT NOT NULL DEFAULT '',
            file_id TEXT NOT NULL,
            file_type TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_assignments (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            assignment_date TEXT NOT NULL,
            delivered_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id),
            UNIQUE(user_id, assignment_date, product_id)
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

        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY,
            categories TEXT NOT NULL DEFAULT '',
            budget_min REAL,
            budget_max REAL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_feedback (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id)
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
        exists = await (await db.execute("SELECT 1 FROM users WHERE user_id=?", (user.id,))).fetchone()
        if exists:
            await db.execute(
                "UPDATE users SET username=?, full_name=?, last_seen=?, blocked=0 WHERE user_id=?",
                (user.username or "", user.full_name or "", ts_now(), user.id)
            )
        else:
            await db.execute("""
                INSERT INTO users(user_id, username, full_name, created_at, last_seen, referrer_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user.id, user.username or "", user.full_name or "", ts_now(), ts_now(), referrer_id))
        await db.commit()


async def access_data(user_id: int) -> tuple[int, int]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        row = await (await db.execute(
            "SELECT access_started, access_until FROM users WHERE user_id=?", (user_id,)
        )).fetchone()
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
                access_started=?, access_until=?, expiry_notice_sent=0
        """, (user_id, current, current, new_started, new_until, new_started, new_until))
        await db.commit()
    return new_until


async def reward_referrer(user_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        row = await (await db.execute(
            "SELECT referrer_id, referral_rewarded FROM users WHERE user_id=?", (user_id,)
        )).fetchone()
        if not row or not row[0] or row[1]:
            return
        referrer_id = int(row[0])
        await db.execute("UPDATE users SET referral_rewarded=1 WHERE user_id=?", (user_id,))
        await db.commit()

    until = await activate_access(referrer_id, REFERRAL_BONUS_DAYS)
    try:
        await bot.send_message(
            referrer_id,
            header("РЕФЕРАЛЬНЫЙ БОНУС") +
            f"\n🎉 Ваш друг оплатил доступ.\n"
            f"Добавлено: <b>{REFERRAL_BONUS_DAYS} день</b>.\n"
            f"Доступ до: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass



def parse_price_number(text: str) -> float | None:
    cleaned = text.replace("€", "").replace("$", "").replace("USDT", "").replace(" ", "").replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


async def get_preferences(user_id: int) -> tuple[list[str], float | None, float | None]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        row = await (await db.execute(
            "SELECT categories, budget_min, budget_max FROM user_preferences WHERE user_id=?",
            (user_id,)
        )).fetchone()
    if not row:
        return [], None, None
    categories = [x.strip() for x in (row[0] or "").split(",") if x.strip()]
    return categories, row[1], row[2]


async def save_preferences(user_id: int, categories: list[str], budget_min: float | None, budget_max: float | None) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO user_preferences(user_id, categories, budget_min, budget_max, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                categories=excluded.categories,
                budget_min=excluded.budget_min,
                budget_max=excluded.budget_max,
                updated_at=excluded.updated_at
        """, (user_id, ",".join(categories), budget_min, budget_max, ts_now()))
        await db.commit()


async def choose_products_for_user(user_id: int, amount: int) -> list[aiosqlite.Row]:
    categories, budget_min, budget_max = await get_preferences(user_id)

    query = """
        SELECT p.*
        FROM product_pool p
        WHERE p.active=1
          AND NOT EXISTS (
              SELECT 1 FROM daily_assignments a
              WHERE a.user_id=? AND a.product_id=p.id
          )
    """
    params: list[Any] = [user_id]

    if categories:
        placeholders = ",".join("?" for _ in categories)
        query += f" AND p.category IN ({placeholders})"
        params.extend(categories)

    if budget_min is not None:
        query += " AND (p.buy_price_num IS NULL OR p.buy_price_num>=?)"
        params.append(budget_min)

    if budget_max is not None:
        query += " AND (p.buy_price_num IS NULL OR p.buy_price_num<=?)"
        params.append(budget_max)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        available = await (await db.execute(query, params)).fetchall()

        # Fallback: if filters are too narrow, use any unseen active products.
        if len(available) < amount:
            available = await (await db.execute("""
                SELECT p.*
                FROM product_pool p
                WHERE p.active=1
                  AND NOT EXISTS (
                      SELECT 1 FROM daily_assignments a
                      WHERE a.user_id=? AND a.product_id=p.id
                  )
            """, (user_id,))).fetchall()

    if not available:
        return []
    return random.sample(available, min(amount, len(available)))


def product_caption(product) -> str:
    return (
        "⚡️ <b>DT TEAM — ПЕРСОНАЛЬНЫЙ ТОВАР</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>{html.escape(product['title'])}</b>\n"
        f"🏷 Категория: <b>{html.escape(product['category'])}</b>\n\n"
        f"{html.escape(product['description'])}\n\n"
        f"💰 <b>Цена:</b> {html.escape(product['sell_price'])}\n\n"
        "🚀 <i>Персональная выдача DT Team</i>"
    )


def feedback_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Продал", callback_data=f"fb:sold:{product_id}"),
            InlineKeyboardButton(text="🕐 Ещё продаю", callback_data=f"fb:selling:{product_id}")
        ],
        [InlineKeyboardButton(text="❌ Не подошёл", callback_data=f"fb:skip:{product_id}")]
    ])


async def send_product(user_id: int, product) -> bool:
    try:
        caption = product_caption(product)
        if product["file_type"] == "document":
            await bot.send_document(user_id, product["file_id"], caption=caption, parse_mode="HTML", reply_markup=feedback_keyboard(product["id"]))
        else:
            await bot.send_photo(user_id, product["file_id"], caption=caption, parse_mode="HTML", reply_markup=feedback_keyboard(product["id"]))

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("""
                INSERT OR IGNORE INTO daily_assignments(user_id, product_id, assignment_date, delivered_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, product["id"], today_key(), ts_now()))
            await db.commit()
        return True
    except Exception as exc:
        logging.warning("Ошибка отправки user=%s product=%s: %s", user_id, product["id"], exc)
        return False


async def already_received_today(user_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        row = await (await db.execute("""
            SELECT COUNT(*) FROM daily_assignments
            WHERE user_id=? AND assignment_date=?
        """, (user_id, today_key()))).fetchone()
    return int(row[0])


async def issue_daily_products(user_id: int) -> int:
    if not await has_access(user_id):
        return 0

    received = await already_received_today(user_id)
    need = max(0, PRODUCTS_PER_DAY - received)
    if need == 0:
        return 0

    products = await choose_products_for_user(user_id, need)
    sent = 0
    for product in products:
        if await send_product(user_id, product):
            sent += 1
        await asyncio.sleep(0.08)
    return sent


async def daily_broadcast() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        users = await (await db.execute("""
            SELECT user_id FROM users WHERE access_until>? AND blocked=0
        """, (ts_now(),))).fetchall()

    total = 0
    for (user_id,) in users:
        total += await issue_daily_products(int(user_id))
    logging.info("Персональная выдача завершена. Отправлено: %s", total)


async def check_invoices() -> None:
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            rows = await (await db.execute("""
                SELECT invoice_id FROM invoices
                WHERE status='active' ORDER BY created_at DESC LIMIT 100
            """)).fetchall()
        if not rows:
            return

        result = await crypto_call("getInvoices", {"invoice_ids": ",".join(str(r[0]) for r in rows)})

        for invoice in result.get("items", []):
            if invoice.get("status") != "paid":
                continue
            invoice_id = int(invoice["invoice_id"])
            async with aiosqlite.connect(DATABASE_PATH) as db:
                db.row_factory = aiosqlite.Row
                stored = await (await db.execute(
                    "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
                )).fetchone()
                if not stored or stored["activated"]:
                    continue
                await db.execute("""
                    UPDATE invoices SET status='paid', paid_at=?, activated=1 WHERE invoice_id=?
                """, (ts_now(), invoice_id))
                await db.commit()

            until = await activate_access(stored["user_id"], ACCESS_DAYS)
            await reward_referrer(stored["user_id"])
            await bot.send_message(
                stored["user_id"],
                header("ДОСТУП АКТИВИРОВАН") +
                f"\n✅ Платёж подтверждён.\n"
                f"📅 Срок: <b>{ACCESS_DAYS} дней</b>\n"
                f"🎲 Каждый день: <b>{PRODUCTS_PER_DAY} случайных уникальных товара</b>\n"
                f"⏳ До: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>",
                parse_mode="HTML",
                reply_markup=client_menu()
            )
            await issue_daily_products(stored["user_id"])
    except Exception:
        logging.exception("Ошибка проверки Crypto Pay")


async def expiry_notices() -> None:
    current = ts_now()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await (await db.execute("""
            SELECT user_id, access_until FROM users
            WHERE access_until>? AND access_until<=?
              AND expiry_notice_sent=0 AND blocked=0
        """, (current, current + 86400))).fetchall()

    for user_id, until in rows:
        try:
            await bot.send_message(
                user_id,
                header("ДОСТУП СКОРО ЗАКОНЧИТСЯ") +
                f"\n⏳ До: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>",
                parse_mode="HTML",
                reply_markup=buy_menu()
            )
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE users SET expiry_notice_sent=1 WHERE user_id=?", (user_id,))
                await db.commit()
        except Exception:
            pass


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    referrer_id = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_"):
        raw = parts[1][4:]
        if raw.isdigit():
            referrer_id = int(raw)
    await register_user(message, referrer_id)

    await message.answer(
        header("ПЕРСОНАЛЬНЫЕ ТОВАРЫ ДЛЯ ПЕРЕПРОДАЖИ") +
        f"\nКаждый день вы получаете <b>{PRODUCTS_PER_DAY} случайных товара</b> из базы DT Team.\n\n"
        "✅ товары не повторяются\n"
        "✅ у каждого пользователя своя подборка\n"
        "✅ фото, описание и цены\n"
        "✅ рекомендации по площадкам\n\n"
        f"💎 <b>{PRICE_USDT} USDT</b> — доступ на <b>{ACCESS_DAYS} дней</b>",
        parse_mode="HTML",
        reply_markup=client_menu()
    )


@router.message(F.text == "🔥 Получить товары")
async def get_products_handler(message: Message) -> None:
    await register_user(message)
    if not await has_access(message.from_user.id):
        await message.answer(header("НЕТ ДОСТУПА") + "\nОформите подписку.", parse_mode="HTML", reply_markup=buy_menu())
        return

    before = await already_received_today(message.from_user.id)
    sent = await issue_daily_products(message.from_user.id)
    after = await already_received_today(message.from_user.id)

    if sent:
        return
    if after >= PRODUCTS_PER_DAY:
        await message.answer("✅ Сегодняшние товары уже выданы. Следующая подборка будет завтра.")
    else:
        await message.answer("⚠️ В базе недостаточно новых товаров. Администратор скоро пополнит базу.")


@router.message(F.text == "💎 Купить доступ")
async def buy_handler(message: Message) -> None:
    await register_user(message)
    await message.answer(
        header("ТАРИФ DT TEAM") +
        f"\n💎 <b>{PRICE_USDT} USDT</b>\n"
        f"📅 <b>{ACCESS_DAYS} дней</b>\n"
        f"🎲 <b>{PRODUCTS_PER_DAY} уникальных товара ежедневно</b>\n"
        f"📦 До <b>{ACCESS_DAYS * PRODUCTS_PER_DAY} товаров</b>",
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
        pay_url = result.get("bot_invoice_url") or result.get("mini_app_invoice_url") or result.get("pay_url")
        invoice_id = int(result["invoice_id"])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("""
                INSERT OR REPLACE INTO invoices(
                    invoice_id, user_id, status, amount, asset, pay_url, created_at, activated
                ) VALUES (?, ?, 'active', ?, 'USDT', ?, ?, 0)
            """, (invoice_id, callback.from_user.id, PRICE_USDT, pay_url, ts_now()))
            await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Перейти к оплате", url=pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить платёж", callback_data=f"payment:check:{invoice_id}")]
        ])
        await bot.send_message(
            callback.from_user.id,
            header("СЧЁТ СОЗДАН") + f"\nСумма: <b>{PRICE_USDT} USDT</b>",
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception:
        logging.exception("Ошибка создания счёта")
        await bot.send_message(callback.from_user.id, f"Ошибка оплаты. Поддержка: {SUPPORT_USERNAME}")


@router.callback_query(F.data.startswith("payment:check:"))
async def manual_check(callback: CallbackQuery) -> None:
    await check_invoices()
    await callback.answer(
        "Доступ активен." if await has_access(callback.from_user.id) else "Платёж пока не найден.",
        show_alert=True
    )


async def profile_text(user_id: int) -> str:
    _, until = await access_data(user_id)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        total = (await (await db.execute(
            "SELECT COUNT(*) FROM daily_assignments WHERE user_id=?", (user_id,)
        )).fetchone())[0]
        refs = (await (await db.execute(
            "SELECT COUNT(*) FROM users WHERE referrer_id=? AND referral_rewarded=1", (user_id,)
        )).fetchone())[0]

    if until <= ts_now():
        return header("ПРОФИЛЬ") + f"\n❌ Нет активного доступа\n📦 Получено ранее: <b>{total}</b>\n🎁 Рефералов: <b>{refs}</b>"
    return (
        header("ПРОФИЛЬ") +
        f"\n✅ Доступ активен\n"
        f"⏳ До: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"📦 Получено товаров: <b>{total}</b>\n"
        f"🎁 Оплативших друзей: <b>{refs}</b>"
    )


@router.message(F.text == "👤 Профиль")
async def profile_handler(message: Message) -> None:
    await message.answer(await profile_text(message.from_user.id), parse_mode="HTML")


@router.callback_query(F.data == "profile:show")
async def profile_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await bot.send_message(callback.from_user.id, await profile_text(callback.from_user.id), parse_mode="HTML")


@router.message(F.text == "📚 Мой архив")
async def archive_handler(message: Message) -> None:
    if not await has_access(message.from_user.id):
        await message.answer("Архив доступен при активной подписке.", reply_markup=buy_menu())
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        products = await (await db.execute("""
            SELECT p.* FROM daily_assignments a
            JOIN product_pool p ON p.id=a.product_id
            WHERE a.user_id=?
            ORDER BY a.delivered_at DESC LIMIT 10
        """, (message.from_user.id,))).fetchall()

    if not products:
        await message.answer("Архив пока пуст.")
        return

    for product in products:
        caption = product_caption(product)
        if product["file_type"] == "document":
            await bot.send_document(message.from_user.id, product["file_id"], caption=caption, parse_mode="HTML")
        else:
            await bot.send_photo(message.from_user.id, product["file_id"], caption=caption, parse_mode="HTML")



@router.message(F.text == "⚙️ Мои настройки")
async def settings_handler(message: Message, state: FSMContext) -> None:
    await state.set_state(PreferencesInput.categories)
    await message.answer(
        "Введите интересующие категории через запятую.\n"
        "Например: Apple, Игры, ПК\n\n"
        "Чтобы получать товары из всех категорий, отправьте: Все"
    )


@router.message(PreferencesInput.categories)
async def preferences_categories(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    categories = [] if text.lower() == "все" else [x.strip() for x in text.split(",") if x.strip()]
    await state.update_data(categories=categories)
    await state.set_state(PreferencesInput.budget)
    await message.answer(
        "Введите бюджет закупки в формате:\n"
        "100-200\n\n"
        "Или отправьте: Без ограничений"
    )


@router.message(PreferencesInput.budget)
async def preferences_budget(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    budget_min = budget_max = None

    if text != "без ограничений":
        match = re.match(r"\s*(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)\s*$", text)
        if not match:
            await message.answer("Неверный формат. Пример: 100-200 или «Без ограничений».")
            return
        budget_min = float(match.group(1).replace(",", "."))
        budget_max = float(match.group(2).replace(",", "."))
        if budget_min > budget_max:
            budget_min, budget_max = budget_max, budget_min

    data = await state.get_data()
    await save_preferences(message.from_user.id, data.get("categories", []), budget_min, budget_max)
    await state.clear()

    categories_text = ", ".join(data.get("categories", [])) or "все"
    budget_text = "без ограничений" if budget_min is None else f"{budget_min:g}–{budget_max:g} €"
    await message.answer(
        header("НАСТРОЙКИ СОХРАНЕНЫ") +
        f"\nКатегории: <b>{html.escape(categories_text)}</b>\n"
        f"Бюджет закупки: <b>{budget_text}</b>",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("fb:"))
async def feedback_handler(callback: CallbackQuery) -> None:
    _, status, product_id_raw = callback.data.split(":", 2)
    if not product_id_raw.isdigit():
        await callback.answer()
        return

    labels = {
        "sold": "Продал",
        "selling": "Ещё продаю",
        "skip": "Не подошёл"
    }
    if status not in labels:
        await callback.answer()
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO product_feedback(user_id, product_id, status, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, product_id) DO UPDATE SET
                status=excluded.status,
                created_at=excluded.created_at
        """, (callback.from_user.id, int(product_id_raw), status, ts_now()))
        await db.commit()

    await callback.answer(f"Отмечено: {labels[status]}", show_alert=True)


@router.message(F.text == "🎁 Пригласить друга")
async def referral_handler(message: Message) -> None:
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        header("РЕФЕРАЛЬНАЯ ПРОГРАММА") +
        f"\nЗа оплатившего друга: <b>{REFERRAL_BONUS_DAYS} день доступа</b>\n\n"
        f"<code>{link}</code>",
        parse_mode="HTML"
    )


@router.message(F.text == "🎟 Промокод")
async def promo_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(PromoInput.code)
    await message.answer("Введите промокод:")


@router.message(PromoInput.code)
async def promo_use(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    await state.clear()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        promo = await (await db.execute(
            "SELECT * FROM promo_codes WHERE code=? AND active=1", (code,)
        )).fetchone()
        already = await (await db.execute(
            "SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, message.from_user.id)
        )).fetchone()
        if not promo or promo["used_count"] >= promo["max_uses"]:
            await message.answer("Промокод недействителен.")
            return
        if already:
            await message.answer("Вы уже использовали этот промокод.")
            return
        await db.execute("INSERT INTO promo_uses VALUES (?, ?, ?)", (code, message.from_user.id, ts_now()))
        await db.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
        await db.commit()

    until = await activate_access(message.from_user.id, int(promo["days"]))
    await message.answer(
        header("ПРОМОКОД АКТИВИРОВАН") +
        f"\nДобавлено дней: <b>{promo['days']}</b>\n"
        f"Доступ до: <b>{datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}</b>",
        parse_mode="HTML"
    )


@router.message(F.text == "💬 Поддержка")
async def support_handler(message: Message) -> None:
    extra = f"\nКанал: {CHANNEL_USERNAME}" if CHANNEL_USERNAME else ""
    await message.answer(header("ПОДДЕРЖКА") + f"\n{SUPPORT_USERNAME}{extra}", parse_mode="HTML")


@router.message(Command("admin"))
async def admin_handler(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer(header("ПАНЕЛЬ АДМИНИСТРАТОРА"), parse_mode="HTML", reply_markup=admin_menu())


@router.callback_query(F.data == "adm:add")
async def add_product_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AddProduct.category)
    await bot.send_message(callback.from_user.id, "Категория товара:")


@router.message(Command("addproduct"))
async def add_product_command(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        await state.set_state(AddProduct.category)
        await message.answer("Категория товара:")


async def text_step(message: Message, state: FSMContext, key: str, next_state: State, prompt: str) -> None:
    if not message.text:
        await message.answer("Отправьте текст.")
        return
    await state.update_data(**{key: message.text.strip()})
    await state.set_state(next_state)
    await message.answer(prompt)


@router.message(AddProduct.category)
async def add_category(m: Message, s: FSMContext):
    await text_step(m, s, "category", AddProduct.title, "Название товара:")


@router.message(AddProduct.title)
async def add_title(m: Message, s: FSMContext):
    await text_step(m, s, "title", AddProduct.description, "Описание товара:")


@router.message(AddProduct.description)
async def add_description(m: Message, s: FSMContext):
    await text_step(m, s, "description", AddProduct.image, "Отправьте картинку товара:")

def extract_file(message: Message):
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return message.document.file_id, "document"
    return None


@router.message(AddProduct.image, F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT}))
async def add_image(message: Message, state: FSMContext) -> None:
    file_data = extract_file(message)
    if not file_data:
        await message.answer("Отправьте картинку товара.")
        return

    await state.update_data(file_id=file_data[0], file_type=file_data[1])
    await state.set_state(AddProduct.price)
    await message.answer("Цена товара:")


@router.message(AddProduct.image)
async def add_image_wrong(message: Message) -> None:
    await message.answer("Нужно отправить картинку как фото или файл.")


@router.message(AddProduct.price)
async def add_price(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправьте цену текстом.")
        return

    data = await state.get_data()
    price_text = message.text.strip()
    price_num = parse_price_number(price_text)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO product_pool(
                title, category, description,
                buy_price, sell_price,
                buy_price_num, sell_price_num,
                marketplaces, german_title, german_description,
                file_id, file_type, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (
            data["title"],
            data["category"],
            data["description"],
            price_text,
            price_text,
            price_num,
            price_num,
            "-",
            "",
            "",
            data["file_id"],
            data["file_type"],
            ts_now()
        ))
        await db.commit()

    await state.clear()
    await message.answer(
        "✅ Товар добавлен в базу.",
        reply_markup=admin_menu()
    )


@router.callback_query(F.data == "adm:pool")
async def pool_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM product_pool WHERE active=1")).fetchone())[0]
        used = (await (await db.execute("SELECT COUNT(*) FROM daily_assignments")).fetchone())[0]
    await bot.send_message(
        callback.from_user.id,
        header("БАЗА ТОВАРОВ") +
        f"\nАктивных товаров: <b>{total}</b>\n"
        f"Всего персональных выдач: <b>{used}</b>\n\n"
        f"Для полного цикла на одного клиента нужно минимум <b>{ACCESS_DAYS * PRODUCTS_PER_DAY}</b> товаров.",
        parse_mode="HTML"
    )


async def stats_text() -> str:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active_users = (await (await db.execute("SELECT COUNT(*) FROM users WHERE access_until>?", (ts_now(),))).fetchone())[0]
        paid = (await (await db.execute("SELECT COUNT(*) FROM invoices WHERE status='paid'")).fetchone())[0]
        revenue = (await (await db.execute(
            "SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM invoices WHERE status='paid' AND asset='USDT'"
        )).fetchone())[0]
        pool = (await (await db.execute("SELECT COUNT(*) FROM product_pool WHERE active=1")).fetchone())[0]
        assignments = (await (await db.execute("SELECT COUNT(*) FROM daily_assignments")).fetchone())[0]
        sold = (await (await db.execute("SELECT COUNT(*) FROM product_feedback WHERE status='sold'")).fetchone())[0]
        skipped = (await (await db.execute("SELECT COUNT(*) FROM product_feedback WHERE status='skip'")).fetchone())[0]
    return (
        header("АНАЛИТИКА") +
        f"\n👥 Пользователей: <b>{users}</b>\n"
        f"✅ Активных подписок: <b>{active_users}</b>\n"
        f"💳 Оплат: <b>{paid}</b>\n"
        f"💰 Выручка: <b>{revenue:g} USDT</b>\n"
        f"📦 Товаров в базе: <b>{pool}</b>\n"
        f"🎲 Персональных выдач: <b>{assignments}</b>\n"
        f"✅ Отметок «Продал»: <b>{sold}</b>\n"
        f"❌ Отметок «Не подошёл»: <b>{skipped}</b>"
    )


@router.callback_query(F.data == "adm:stats")
async def stats_callback(callback: CallbackQuery) -> None:
    if is_admin(callback.from_user.id):
        await callback.answer()
        await bot.send_message(callback.from_user.id, await stats_text(), parse_mode="HTML")


@router.message(Command("stats"))
async def stats_command(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer(await stats_text(), parse_mode="HTML")


@router.callback_query(F.data == "adm:send")
async def send_callback(callback: CallbackQuery) -> None:
    if is_admin(callback.from_user.id):
        await callback.answer("Выдача запущена")
        await daily_broadcast()
        await bot.send_message(callback.from_user.id, "✅ Персональная выдача завершена.")


@router.message(Command("sendtoday"))
async def send_today_command(message: Message) -> None:
    if is_admin(message.from_user.id):
        await daily_broadcast()
        await message.answer("✅ Персональная выдача завершена.")


@router.callback_query(F.data == "adm:broadcast")
async def broadcast_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if is_admin(callback.from_user.id):
        await callback.answer()
        await state.set_state(BroadcastInput.content)
        await bot.send_message(callback.from_user.id, "Отправьте сообщение для общей рассылки.")


@router.message(BroadcastInput.content)
async def broadcast_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        users = await (await db.execute("SELECT user_id FROM users WHERE blocked=0")).fetchall()
    sent = 0
    for (user_id,) in users:
        try:
            await bot.copy_message(user_id, message.chat.id, message.message_id)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    await message.answer(f"✅ Доставлено: {sent}")


@router.message(Command("grant"))
async def grant_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Формат: /grant USER_ID DAYS")
        return
    until = await activate_access(int(parts[1]), int(parts[2]))
    await message.answer(f"✅ Доступ до {datetime.fromtimestamp(until, TZ).strftime('%d.%m.%Y %H:%M')}")


@router.message(Command("promo"))
async def promo_create(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await message.answer("Формат: /promo CODE DAYS USES")
        return
    code, days, uses = parts[1].upper(), int(parts[2]), int(parts[3])
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO promo_codes(code, days, max_uses, used_count, active, created_at)
            VALUES (?, ?, ?, 0, 1, ?)
        """, (code, days, uses, ts_now()))
        await db.commit()
    await message.answer(f"✅ Промокод {code} создан.")


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Операция отменена.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    await init_db()

    scheduler.add_job(daily_broadcast, "cron", hour=SEND_HOUR, minute=SEND_MINUTE, id="daily", replace_existing=True)
    scheduler.add_job(check_invoices, "interval", seconds=PAYMENT_CHECK_SECONDS, id="payments", replace_existing=True)
    scheduler.add_job(expiry_notices, "interval", hours=1, id="expiry", replace_existing=True)
    scheduler.start()

    await crypto_call("getMe")
    logging.info("DT Team Random Pro запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
