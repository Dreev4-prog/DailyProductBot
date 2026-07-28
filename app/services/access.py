from app.config import settings
from app.database import connect, now_ts


async def activate_access(user_id: int, days: int | None = None) -> int:
    days = days or settings.access_days
    current = now_ts()
    db = await connect()
    try:
        row = await (await db.execute("SELECT access_until FROM users WHERE user_id=?", (user_id,))).fetchone()
        base = max(current, row["access_until"] if row else 0)
        until = base + days * 86400
        await db.execute("""
            UPDATE users SET access_until=?, expiry_notice_sent=0 WHERE user_id=?
        """, (until, user_id))
        await db.commit()
        return until
    finally:
        await db.close()
