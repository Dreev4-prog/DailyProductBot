import asyncio
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import asyncpg

from app.config import settings


def now_ts() -> int:
    return int(time.time())


class Cursor:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0, lastrowid: int | None = None):
        self._rows = rows or []
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class PostgresConnection:
    def __init__(self, pool: asyncpg.Pool, connection: asyncpg.Connection):
        self.pool = pool
        self.connection = connection
        self._closed = False

    @staticmethod
    def _translate(sql: str, params: Iterable[Any]) -> tuple[str, tuple[Any, ...]]:
        query = sql.strip()
        values = tuple(params)

        # SQLite compatibility used throughout the existing handlers.
        ignore_insert = bool(re.match(r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO", query))
        if ignore_insert:
            query = re.sub(r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", query, count=1)
            if "ON CONFLICT" not in query.upper():
                query = query.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        index = 0
        def replace_placeholder(_: re.Match) -> str:
            nonlocal index
            index += 1
            return f"${index}"

        query = re.sub(r"\?", replace_placeholder, query)
        return query, values

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> Cursor:
        query, values = self._translate(sql, params)
        upper = query.lstrip().upper()

        # The add-product flow expects cursor.lastrowid.
        if upper.startswith("INSERT INTO PRODUCTS") and "RETURNING" not in upper:
            row = await self.connection.fetchrow(query.rstrip().rstrip(";") + " RETURNING id", *values)
            return Cursor(lastrowid=int(row["id"]), rowcount=1)

        if upper.startswith("SELECT") or " RETURNING " in upper:
            rows = list(await self.connection.fetch(query, *values))
            return Cursor(rows=rows, rowcount=len(rows))

        status = await self.connection.execute(query, *values)
        match = re.search(r"(\d+)$", status)
        rowcount = int(match.group(1)) if match else 0
        return Cursor(rowcount=rowcount)

    async def executemany(self, sql: str, args: Iterable[Iterable[Any]]) -> None:
        rows = [tuple(row) for row in args]
        if not rows:
            return
        query, _ = self._translate(sql, rows[0])
        await self.connection.executemany(query, rows)

    async def commit(self) -> None:
        # asyncpg commits each statement unless an explicit transaction is opened.
        return None

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self.pool.release(self.connection)


_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            if not settings.database_url:
                raise RuntimeError(
                    "DATABASE_URL не задан. Добавьте PostgreSQL в Railway и подключите "
                    "его DATABASE_URL к сервису worker."
                )
            _pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=1,
                max_size=settings.database_pool_size,
                command_timeout=60,
            )
    return _pool


async def connect() -> PostgresConnection:
    pool = await _get_pool()
    connection = await pool.acquire()
    return PostgresConnection(pool, connection)


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    full_name TEXT NOT NULL DEFAULT '',
    created_at BIGINT NOT NULL,
    last_seen BIGINT NOT NULL,
    access_until BIGINT NOT NULL DEFAULT 0,
    referrer_id BIGINT,
    referral_rewarded INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    expiry_notice_sent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    categories TEXT NOT NULL DEFAULT '',
    budget_min DOUBLE PRECISION,
    budget_max DOUBLE PRECISION,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    image_file_id TEXT NOT NULL,
    image_type TEXT NOT NULL DEFAULT 'photo',
    price_text TEXT NOT NULL,
    price_num DOUBLE PRECISION,
    active INTEGER NOT NULL DEFAULT 1,
    created_at BIGINT NOT NULL,
    deleted_at BIGINT,
    tags TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS product_images (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    image_type TEXT NOT NULL DEFAULT 'photo',
    position INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    UNIQUE(product_id, position)
);

CREATE TABLE IF NOT EXISTS assignments (
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    assignment_date TEXT NOT NULL,
    delivered_at BIGINT NOT NULL,
    PRIMARY KEY(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS invoices (
    provider TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    client_invoice_id TEXT,
    status TEXT NOT NULL,
    amount TEXT NOT NULL,
    asset TEXT NOT NULL,
    pay_url TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    paid_at BIGINT,
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
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_uses (
    code TEXT NOT NULL REFERENCES promo_codes(code) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    used_at BIGINT NOT NULL,
    PRIMARY KEY(code, user_id)
);

CREATE TABLE IF NOT EXISTS bot_admins (
    user_id BIGINT PRIMARY KEY,
    added_by BIGINT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id BIGSERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    sent_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_user_date ON assignments(user_id, assignment_date);
CREATE INDEX IF NOT EXISTS idx_products_category_active ON products(category, active);
CREATE INDEX IF NOT EXISTS idx_products_title_lower ON products(LOWER(title));
CREATE INDEX IF NOT EXISTS idx_products_deleted_at ON products(deleted_at);
CREATE INDEX IF NOT EXISTS idx_invoices_pending ON invoices(provider, activated, created_at);
"""


async def _sqlite_rows(path: Path, table: str) -> list[dict[str, Any]]:
    def read() -> list[dict[str, Any]]:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                return []
            return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()]
        finally:
            connection.close()
    return await asyncio.to_thread(read)


async def migrate_sqlite_if_available() -> None:
    if not settings.migrate_sqlite_on_start:
        return
    path = settings.sqlite_migration_path
    if not path.exists() or path.stat().st_size == 0:
        logging.info("SQLite-файл для переноса не найден: %s", path)
        return

    pool = await _get_pool()
    async with pool.acquire() as connection:
        already = await connection.fetchval(
            "SELECT setting_value FROM bot_settings WHERE setting_key='sqlite_migration_completed'"
        )
        if already == "1":
            return

        table_order = [
            "users", "user_preferences", "products", "product_images", "assignments",
            "favorites", "feedback", "invoices", "promo_codes", "promo_uses",
            "bot_admins", "broadcasts", "bot_settings",
        ]
        async with connection.transaction():
            for table in table_order:
                rows = await _sqlite_rows(path, table)
                if not rows:
                    continue
                columns = list(rows[0].keys())
                placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
                quoted_columns = ", ".join(f'"{column}"' for column in columns)
                sql = (
                    f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders}) '
                    "ON CONFLICT DO NOTHING"
                )
                await connection.executemany(sql, [tuple(row.get(column) for column in columns) for row in rows])
                logging.info("Перенесено из SQLite: %s — %s строк", table, len(rows))

            for table in ("products", "product_images", "broadcasts"):
                await connection.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
                )

            await connection.execute(
                """
                INSERT INTO bot_settings(setting_key, setting_value, updated_at)
                VALUES ('sqlite_migration_completed', '1', $1)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value='1', updated_at=EXCLUDED.updated_at
                """,
                now_ts(),
            )
        logging.info("Перенос SQLite → PostgreSQL завершён")


async def init_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as connection:
        await connection.execute(POSTGRES_SCHEMA)
        # Compatibility for databases created by an earlier 4.0 build.
        await connection.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS deleted_at BIGINT")
        await connection.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT ''")
        await connection.execute(
            """
            INSERT INTO product_images(product_id, file_id, image_type, position, created_at)
            SELECT id, image_file_id, image_type, 0, created_at
            FROM products
            WHERE image_file_id <> ''
            ON CONFLICT(product_id, position) DO NOTHING
            """
        )
    await migrate_sqlite_if_available()


async def get_bot_setting(key: str, default: str = "") -> str:
    db = await connect()
    try:
        row = await (await db.execute(
            "SELECT setting_value FROM bot_settings WHERE setting_key=?",
            (key,),
        )).fetchone()
        return str(row["setting_value"]) if row else default
    finally:
        await db.close()


async def set_bot_setting(key: str, value: str) -> None:
    db = await connect()
    try:
        await db.execute(
            """
            INSERT INTO bot_settings(setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value=excluded.setting_value,
                updated_at=excluded.updated_at
            """,
            (key, value, now_ts()),
        )
        await db.commit()
    finally:
        await db.close()
