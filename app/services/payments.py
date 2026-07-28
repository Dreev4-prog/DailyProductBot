import json
import logging
import uuid
from typing import Any

import aiohttp

from app.core import bot
from app.config import settings
from app.database import connect, now_ts
from app.services.access import activate_access
from app.services.products import issue_products
from app.utils import brand_header


def crypto_base() -> str:
    return "https://testnet-pay.crypt.bot/api" if settings.crypto_pay_network == "testnet" else "https://pay.crypt.bot/api"


def xrocket_base() -> str:
    return (
        "https://pay.api.testnet.xrocket.exchange"
        if settings.xrocket_network == "testnet"
        else "https://pay.api.xrocket.exchange"
    )


async def crypto_call(method: str, data: dict[str, Any] | None = None):
    headers = {"Crypto-Pay-API-Token": settings.crypto_pay_token}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(f"{crypto_base()}/{method}", data=data or {}) as response:
            payload = await response.json(content_type=None)
            if response.status != 200 or not payload.get("ok"):
                raise RuntimeError(f"Crypto Pay: {payload}")
            return payload["result"]


async def xrocket_request(method: str, path: str, payload: dict | None = None, params: dict | None = None):
    headers = {
        "Authorization": f"Bearer {settings.xrocket_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"{xrocket_base()}{path}"

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.request(method, url, json=payload, params=params, allow_redirects=True) as response:
            raw_text = await response.text()
            content_type = response.headers.get("Content-Type", "")

            try:
                body = json.loads(raw_text) if raw_text.strip() else {}
            except json.JSONDecodeError:
                preview = raw_text[:300].replace("\n", " ")
                raise RuntimeError(
                    f"xRocket вернул не JSON. HTTP {response.status}, "
                    f"Content-Type={content_type!r}, URL={url}, ответ={preview!r}"
                )

            if response.status >= 400:
                raise RuntimeError(
                    f"xRocket HTTP {response.status}, URL={url}: {body}"
                )

            return body


def pick(data: dict, *keys, default=None):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


async def validate_payment_connections() -> None:
    if settings.crypto_pay_enabled:
        try:
            await crypto_call("getMe")
            logging.info("Crypto Pay подключён")
        except Exception:
            logging.exception("Crypto Pay не прошёл проверку при запуске")

    if settings.xrocket_enabled:
        try:
            await xrocket_request("GET", "/api/v1/balances")
            logging.info("xRocket подключён")
        except Exception:
            # Платёжный сервис не должен останавливать Telegram-бот и веб-панель.
            # Подробная причина останется в Railway Deploy Logs.
            logging.exception("xRocket не прошёл проверку при запуске")


async def create_crypto_invoice(user_id: int):
    result = await crypto_call("createInvoice", {
        "asset": "USDT",
        "amount": settings.price_usdt,
        "description": f"DT Team — {settings.access_days} days",
        "payload": f"dtv2:{user_id}:{now_ts()}",
        "expires_in": 3600,
        "allow_comments": "false",
        "allow_anonymous": "false",
    })
    invoice_id = str(result["invoice_id"])
    pay_url = result.get("bot_invoice_url") or result.get("mini_app_invoice_url") or result.get("pay_url")
    await store_invoice("crypto", invoice_id, user_id, None, "active", pay_url, result)
    return invoice_id, pay_url


async def create_xrocket_invoice(user_id: int):
    client_id = f"dtv2-{user_id}-{uuid.uuid4().hex[:12]}"
    # Поля соответствуют общей модели xRocket Pay API. В случае изменения API
    # ответ и ошибка сохраняются в Railway logs.
    payload = {
        "amount": float(settings.price_usdt),
        "currency": settings.xrocket_asset,
        "asset": settings.xrocket_asset,
        "description": f"DT Team — {settings.access_days} days",
        "clientInvoiceId": client_id,
        "customData": str(user_id),
    }
    result = await xrocket_request("POST", "/api/v1/invoices", payload)
    data = result.get("data", result)
    invoice_id = str(pick(data, "invoiceId", "id"))
    pay_url = pick(data, "link", "payUrl", "url", "botLink")
    if not invoice_id or not pay_url:
        raise RuntimeError(f"xRocket: неизвестный формат ответа {result}")
    status = str(pick(data, "status", default="active")).lower()
    await store_invoice("xrocket", invoice_id, user_id, client_id, status, pay_url, result)
    return invoice_id, pay_url


async def store_invoice(provider, invoice_id, user_id, client_id, status, pay_url, raw):
    db = await connect()
    try:
        await db.execute("""
            INSERT OR REPLACE INTO invoices(
                provider, invoice_id, user_id, client_invoice_id, status,
                amount, asset, pay_url, created_at, activated, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """, (
            provider, invoice_id, user_id, client_id, status,
            settings.price_usdt, "USDT", pay_url, now_ts(),
            json.dumps(raw, ensure_ascii=False),
        ))
        await db.commit()
    finally:
        await db.close()


async def pending_invoices(provider: str):
    db = await connect()
    try:
        return await (await db.execute("""
            SELECT * FROM invoices WHERE provider=? AND activated=0
            ORDER BY created_at DESC LIMIT 100
        """, (provider,))).fetchall()
    finally:
        await db.close()


async def activate_paid(row) -> None:
    db = await connect()
    try:
        changed = await db.execute("""
            UPDATE invoices SET status='paid', paid_at=?, activated=1
            WHERE provider=? AND invoice_id=? AND activated=0
        """, (now_ts(), row["provider"], row["invoice_id"]))
        await db.commit()
        if changed.rowcount != 1:
            return
    finally:
        await db.close()

    until = await activate_access(row["user_id"])
    await bot.send_message(
        row["user_id"],
        brand_header("ДОСТУП АКТИВИРОВАН") +
        f"\n✅ Оплата через <b>{row['provider']}</b> подтверждена.\n"
        f"Доступ активен на <b>{settings.access_days} дней</b>.",
        parse_mode="HTML",
    )
    await issue_products(row["user_id"])


async def check_crypto_invoices() -> None:
    rows = await pending_invoices("crypto")
    if not rows:
        return
    ids = ",".join(row["invoice_id"] for row in rows)
    result = await crypto_call("getInvoices", {"invoice_ids": ids})
    statuses = {str(item["invoice_id"]): item.get("status") for item in result.get("items", [])}
    for row in rows:
        if statuses.get(row["invoice_id"]) == "paid":
            await activate_paid(row)


async def check_xrocket_invoices() -> None:
    for row in await pending_invoices("xrocket"):
        params = {"invoiceId": row["invoice_id"]}
        result = await xrocket_request("GET", "/api/v1/invoice", params=params)
        data = result.get("data", result)
        status = str(pick(data, "status", "invoiceStatus", default="")).lower()
        if status in {"paid", "completed", "success", "successful"}:
            await activate_paid(row)


async def check_all_invoices() -> None:
    try:
        if settings.crypto_pay_enabled:
            await check_crypto_invoices()
        if settings.xrocket_enabled:
            await check_xrocket_invoices()
    except Exception:
        logging.exception("Ошибка проверки платежей")
