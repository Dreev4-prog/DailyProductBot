import time
from pathlib import Path

import aiosqlite

from app.config import settings


def now_ts() -> int:
    return int(time.time())


async def connect() -> aiosqlite.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    db = await connect()
    try:
        await db.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            access_until INTEGER NOT NULL DEFAULT 0,
            referrer_id INTEGER,
            referral_rewarded INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            expiry_notice_sent INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            categories TEXT NOT NULL DEFAULT '',
            budget_min REAL,
            budget_max REAL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            image_file_id TEXT NOT NULL,
            image_type TEXT NOT NULL DEFAULT 'photo',
            price_text TEXT NOT NULL,
            price_num REAL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assignments (
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            assignment_date TEXT NOT NULL,
            delivered_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id)
        );

        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id)
        );

        CREATE TABLE IF NOT EXISTS invoices (
            provider TEXT NOT NULL,
            invoice_id TEXT NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            client_invoice_id TEXT,
            status TEXT NOT NULL,
            amount TEXT NOT NULL,
            asset TEXT NOT NULL,
            pay_url TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            paid_at INTEGER,
            activated INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(provider, invoice_id)
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
            code TEXT NOT NULL REFERENCES promo_codes(code) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            used_at INTEGER NOT NULL,
            PRIMARY KEY(code, user_id)
        );

        CREATE TABLE IF NOT EXISTS bot_admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        """)

        columns = await (await db.execute("PRAGMA table_info(products)")).fetchall()
        column_names = {row["name"] for row in columns}
        if "deleted_at" not in column_names:
            await db.execute("ALTER TABLE products ADD COLUMN deleted_at INTEGER")
        await db.commit()
    finally:
        await db.close()
