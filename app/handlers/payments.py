import html

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.services.payments import create_crypto_invoice, create_xrocket_invoice, check_all_invoices
from app.utils import brand_header

router = Router()


@router.callback_query(F.data.startswith("pay:create:"))
async def create_invoice(callback: CallbackQuery) -> None:
    await callback.answer()
    provider = callback.data.split(":")[-1]
    try:
        if provider == "crypto":
            invoice_id, pay_url = await create_crypto_invoice(callback.from_user.id)
            title = "Crypto Bot"
        elif provider == "xrocket":
            invoice_id, pay_url = await create_xrocket_invoice(callback.from_user.id)
            title = "xRocket"
        else:
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить через {title}", url=pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="pay:check")],
        ])
        await callback.message.answer(
            brand_header("СЧЁТ СОЗДАН") +
            f"\nПровайдер: <b>{html.escape(title)}</b>\n"
            f"Сумма: <b>{settings.price_usdt} USDT</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        await callback.message.answer(
            "Не удалось создать счёт. Проверьте настройки платёжного сервиса или обратитесь в поддержку."
        )


@router.callback_query(F.data == "pay:check")
async def manual_check(callback: CallbackQuery) -> None:
    await check_all_invoices()
    await callback.answer("Проверка выполнена.", show_alert=True)


@router.message(F.text == "/paysupport")
async def pay_support(message: Message) -> None:
    await message.answer(f"Поддержка по оплате: {settings.support_username}")
