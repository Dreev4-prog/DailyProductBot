from app.core import scheduler
from app.config import settings
from app.services.payments import check_all_invoices
from app.services.products import daily_distribution


def configure_scheduler() -> None:
    scheduler.add_job(
        daily_distribution,
        "cron",
        hour=settings.send_hour,
        minute=settings.send_minute,
        id="daily_distribution",
        replace_existing=True,
    )
    scheduler.add_job(
        check_all_invoices,
        "interval",
        seconds=30,
        id="payment_check",
        replace_existing=True,
    )
