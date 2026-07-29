from app.core import scheduler
from app.services.payments import check_all_invoices


def configure_scheduler() -> None:
    scheduler.add_job(
        check_all_invoices,
        "interval",
        seconds=30,
        id="payment_check",
        replace_existing=True,
    )
