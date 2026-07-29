import asyncio
import random
from datetime import datetime

from app.core import bot
from app.config import settings
from app.database import connect, now_ts
from app.keyboards import product_actions
from app.utils import product_caption


def today_key() -> str:
    return datetime.now(settings.timezone).date().isoformat()


async def has_access(user_id: int) -> bool:
    db = await connect()
    try:
        row = await (await db.execute("SELECT access_until FROM users WHERE user_id=?", (user_id,))).fetchone()
        return bool(row and row["access_until"] > now_ts())
    finally:
        await db.close()


async def already_today(user_id: int) -> int:
    db = await connect()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) c FROM assignments WHERE user_id=? AND assignment_date=?",
            (user_id, today_key()),
        )).fetchone()
        return int(row["c"])
    finally:
        await db.close()


async def select_product_by_category(user_id: int, category: str):
    db = await connect()
    try:
        rows = await (await db.execute(
            """
            SELECT p.* FROM products p
            WHERE p.active=1
              AND p.deleted_at IS NULL
              AND p.category=?
              AND NOT EXISTS (
                SELECT 1 FROM assignments a
                WHERE a.user_id=? AND a.product_id=p.id
              )
            """,
            (category, user_id),
        )).fetchall()
        return random.choice(rows) if rows else None
    finally:
        await db.close()


async def send_product(user_id: int, product) -> bool:
    try:
        kwargs = dict(
            chat_id=user_id,
            caption=product_caption(product),
            parse_mode="HTML",
            reply_markup=product_actions(product["id"]),
        )
        if product["image_type"] == "document":
            await bot.send_document(document=product["image_file_id"], **kwargs)
        else:
            await bot.send_photo(photo=product["image_file_id"], **kwargs)

        db = await connect()
        try:
            await db.execute(
                """
                INSERT OR IGNORE INTO assignments(
                    user_id, product_id, assignment_date, delivered_at
                ) VALUES (?, ?, ?, ?)
                """,
                (user_id, product["id"], today_key(), now_ts()),
            )
            await db.commit()
        finally:
            await db.close()
        return True
    except Exception:
        return False


_issue_locks: dict[int, asyncio.Lock] = {}


async def issue_one_product(user_id: int, category: str) -> str:
    """Выдаёт ровно один товар из выбранной категории.

    Возвращает: sent, no_access, daily_limit, empty или failed.
    """
    lock = _issue_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if not await has_access(user_id):
            return "no_access"
        if await already_today(user_id) >= settings.products_per_day:
            return "daily_limit"

        product = await select_product_by_category(user_id, category)
        if not product:
            return "empty"
        return "sent" if await send_product(user_id, product) else "failed"


async def daily_distribution() -> None:
    # В версии 2.2 автоматическая выдача отключена:
    # пользователь сам выбирает категорию для каждого из двух товаров.
    return None
