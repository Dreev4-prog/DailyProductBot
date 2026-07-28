import html
import re


def brand_header(title: str) -> str:
    return f"⚡️ <b>DT TEAM</b>\n━━━━━━━━━━━━━━━━━━\n<b>{html.escape(title)}</b>\n"


def parse_price(text: str) -> float | None:
    cleaned = text.replace("€", "").replace("$", "").replace("USDT", "").replace(" ", "").replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def product_caption(product) -> str:
    return (
        "⚡️ <b>DT TEAM — ПЕРСОНАЛЬНЫЙ ТОВАР</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>{html.escape(product['category'])}</b>\n"
        f"📦 <b>{html.escape(product['title'])}</b>\n\n"
        f"{html.escape(product['description'])}\n\n"
        f"💰 <b>Цена:</b> {html.escape(product['price_text'])}\n\n"
        "🚀 <i>Персональная выдача DT Team</i>"
    )
