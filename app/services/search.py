import re
from typing import Any

from app.database import connect


def normalize_query(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip().lower())
    return value[:120]


def query_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[\w\-]+", normalize_query(value), flags=re.UNICODE) if len(token) >= 2][:8]


async def search_products(query: str, limit: int = 20, include_deleted: bool = False) -> list[Any]:
    normalized = normalize_query(query)
    if not normalized:
        return []
    tokens = query_tokens(normalized)
    conditions = ["LOWER(title) LIKE ?", "LOWER(description) LIKE ?", "LOWER(category) LIKE ?", "LOWER(tags) LIKE ?"]
    params: list[Any] = [f"%{normalized}%", f"%{normalized}%", f"%{normalized}%", f"%{normalized}%"]
    for token in tokens:
        conditions.append("LOWER(title) LIKE ?")
        params.append(f"%{token}%")
    deleted_clause = "" if include_deleted else "AND deleted_at IS NULL"
    sql = f"""
        SELECT *,
               CASE WHEN LOWER(title)=? THEN 100
                    WHEN LOWER(title) LIKE ? THEN 80
                    ELSE 40 END AS relevance
        FROM products
        WHERE ({' OR '.join(conditions)}) {deleted_clause}
        ORDER BY relevance DESC, id DESC
        LIMIT ?
    """
    all_params = [normalized, f"%{normalized}%", *params, limit]
    db = await connect()
    try:
        return await (await db.execute(sql, tuple(all_params))).fetchall()
    finally:
        await db.close()


async def find_similar_products(title: str, limit: int = 5) -> list[Any]:
    normalized = normalize_query(title)
    if len(normalized) < 3:
        return []
    tokens = query_tokens(normalized)
    conditions = ["LOWER(title)=?", "LOWER(title) LIKE ?"]
    params: list[Any] = [normalized, f"%{normalized}%"]
    for token in tokens:
        conditions.append("LOWER(title) LIKE ?")
        params.append(f"%{token}%")
    db = await connect()
    try:
        return await (await db.execute(
            f"""
            SELECT * FROM products
            WHERE deleted_at IS NULL AND ({' OR '.join(conditions)})
            ORDER BY CASE WHEN LOWER(title)=? THEN 0 ELSE 1 END, id DESC
            LIMIT ?
            """,
            tuple([*params, normalized, limit]),
        )).fetchall()
    finally:
        await db.close()
