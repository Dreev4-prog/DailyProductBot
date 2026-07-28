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


async def preferences(user_id: int):
    db = await connect()
    try:
        row = await (await db.execute(
            "SELECT categories, budget_min, budget_max FROM user_preferences WHERE user_id=?",
            (user_id,),
        )).fetchone()
        if not row:
            return [], None, None
        return [x for x in row["categories"].split(",") if x], row["budget_min"], row["budget_max"]
    finally:
        await db.close()


async def already_today(user_id: int) -> int:
    db = await connect()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) c FROM assignments WHERE user_id=? AND assignment_date=?",
            (user_id, today_key()),
        )).fetchone()
        return row["c"]
    finally:
        await db.close()


async def select_products(user_id: int, amount: int):
    categories, budget_min, budget_max = await preferences(user_id)
    query = """
        SELECT p.* FROM products p
        WHERE p.active=1
          AND NOT EXISTS (
            SELECT 1 FROM assignments a WHERE a.user_id=? AND a.product_id=p.id
          )
    """
    params = [user_id]
    if categories:
        query += f" AND p.category IN ({','.join('?' for _ in categories)})"
        params.extend(categories)
    if budget_min is not None:
        query += " AND (p.price_num IS NULL OR p.price_num>=?)"
        params.append(budget_min)
    if budget_max is not None:
        query += " AND (p.price_num IS NULL OR p.price_num<=?)"
        params.append(budget_max)

    db = await connect()
    try:
        rows = await (await db.execute(query, params)).fetchall()
        if len(rows) < amount:
            rows = await (await db.execute("""
                SELECT p.* FROM products p
                WHERE p.active=1
                  AND NOT EXISTS (
                    SELECT 1 FROM assignments a WHERE a.user_id=? AND a.product_id=p.id
                  )
            """, (user_id,))).fetchall()
        return random.sample(rows, min(amount, len(rows))) if rows else []
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
            await db.execute("""
                INSERT OR IGNORE INTO assignments(user_id, product_id, assignment_date, delivered_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, product["id"], today_key(), now_ts()))
            await db.commit()
        finally:
            await db.close()
        return True
    except Exception:
        return False


async def issue_products(user_id: int) -> int:
    if not await has_access(user_id):
        return 0
    need = max(0, settings.products_per_day - await already_today(user_id))
    products = await select_products(user_id, need)
    sent = 0
    for product in products:
        sent += int(await send_product(user_id, product))
        await asyncio.sleep(0.08)
    return sent


async def daily_distribution() -> None:
    db = await connect()
    try:
        users = await (await db.execute(
            "SELECT user_id FROM users WHERE access_until>? AND blocked=0",
            (now_ts(),),
        )).fetchall()
    finally:
        await db.close()
    for user in users:
        await issue_products(user["user_id"])
