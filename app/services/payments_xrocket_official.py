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
    # Актуальный production URL официального xRocket Pay SDK.
    # Для mainnet используется https://pay.xrocket.tg
    # Testnet оставлен отдельным адресом, но рабочая конфигурация пользователя — mainnet.
    return (
        "https://pay.testnet.xrocket.tg"
        if settings.xrocket_network == "testnet"
        else "https://pay.xrocket.tg"
    )


async def crypto_call(method: str, data: dict[str, Any] | None = None):
    headers = {"Crypto-Pay-API-Token": settings.crypto_pay_token}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(f"{crypto_base()}/{method}", data=data or {}) as response:
            payload = await response.json(content_type=None)
            if response.status != 200 or not payload.get("ok"):
                raise RuntimeError(f"Crypto Pay: {payload}")
            return payload["result"]


async def xrocket_request(
    method: str,
    path: str,
    payload: dict | None = None,
    params: dict | None = None,
):
    headers = {
        "Rocket-Pay-Key": settings.xrocket_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"{xrocket_base().rstrip('/')}/{path.lstrip('/')}"

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.request(
            method,
            url,
            json=payload,
            params=params,
            allow_redirects=True,
        ) as response:
            raw_text = await response.text()
            content_type = response.headers.get("Content-Type", "")

            try:
                body = json.loads(raw_text) if raw_text.strip() else {}
            except json.JSONDecodeError as exc:
                preview = raw_text[:400].replace("\n", " ")
                raise RuntimeError(
                    f"xRocket вернул не JSON. HTTP {response.status}, "
                    f"Content-Type={content_type!r}, URL={url}, ответ={preview!r}"
                ) from exc

            if response.status >= 400:
                raise RuntimeError(
                    f"xRocket HTTP {response.status}, URL={url}: {body}"
                )

            return body


def pick(data: dict, *keys, default=None):
    for key in keys:
        if isinstance(data, dict) and key in data and data[key] is not None:
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
            result = await xrocket_request("GET", "/app/info")
            app_data = result.get("data", result) if isinstance(result, dict) else result
            app_name = pick(app_data, "name", "appName", default="xRocket app")
            logging.info("xRocket подключён: %s", app_name)
        except Exception:
            # Ошибка xRocket не должна останавливать Telegram-бот.
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
    pay_url = (
        result.get("bot_invoice_url")
        or result.get("mini_app_invoice_url")
        or result.get("pay_url")
    )
    await store_invoice(
        "crypto", invoice_id, user_id, None, "active", pay_url, result
    )
    return invoice_id, pay_url


async def create_xrocket_invoice(user_id: int):
    client_id = f"dtv2-{user_id}-{uuid.uuid4().hex[:12]}"

    payload = {
        "amount": float(settings.price_usdt),
        "currency": settings.xrocket_asset.upper(),
        "description": f"DT Team — доступ на {settings.access_days} дней",
        "numPayments": 1,
        "expiredIn": 3600,
    }

    result = await xrocket_request("POST", "/tg-invoices", payload=payload)
    data = result.get("data", result)

    invoice_id = pick(data, "id", "invoiceId", "_id")
    pay_url = pick(data, "link", "url", "payUrl", "botLink")

    if invoice_id is None or not pay_url:
        raise RuntimeError(
            f"xRocket создал счёт, но вернул неизвестный формат: {result}"
        )

    status = str(pick(data, "status", default="active")).lower()

    await store_invoice(
        "xrocket",
        str(invoice_id),
        user_id,
        client_id,
        status,
        pay_url,
        result,
    )
    return str(invoice_id), pay_url


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
        result = await xrocket_request(
            "GET",
            f"/tg-invoices/{row['invoice_id']}",
        )
        data = result.get("data", result)

        status = str(
            pick(data, "status", "invoiceStatus", default="")
        ).lower()

        paid_payments = pick(
            data,
            "paidPayments",
            "paymentsCount",
            "paidCount",
            default=0,
        )
        required_payments = pick(
            data,
            "numPayments",
            "paymentsNumber",
            default=1,
        )

        try:
            is_paid_by_count = int(paid_payments) >= int(required_payments)
        except (TypeError, ValueError):
            is_paid_by_count = False

        if status in {
            "paid",
            "completed",
            "success",
            "successful",
            "finished",
        } or is_paid_by_count:
            await activate_paid(row)


async def check_all_invoices() -> None:
    try:
        if settings.crypto_pay_enabled:
            await check_crypto_invoices()
        if settings.xrocket_enabled:
            await check_xrocket_invoices()
    except Exception:
        logging.exception("Ошибка проверки платежей")
