import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "").strip()
    admin_ids: frozenset[int] = frozenset(
        int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
        if x.strip().isdigit()
    )
    database_path: Path = Path(os.getenv("DATABASE_PATH", "/app/data/dt_team_v2.db"))
    banner_path: Path = Path(os.getenv("BANNER_PATH", "assets/dt_products_banner.jpg"))

    brand_name: str = os.getenv("BRAND_NAME", "DT Team")
    support_username: str = os.getenv("SUPPORT_USERNAME", "@support")
    channel_username: str = os.getenv("CHANNEL_USERNAME", "")
    timezone_name: str = os.getenv("TIMEZONE", "Europe/Rome")
    send_hour: int = int(os.getenv("SEND_HOUR", "10"))
    send_minute: int = int(os.getenv("SEND_MINUTE", "0"))

    price_usdt: str = os.getenv("PRICE_USDT", "20")
    access_days: int = int(os.getenv("ACCESS_DAYS", "5"))
    products_per_day: int = int(os.getenv("PRODUCTS_PER_DAY", "2"))
    referral_bonus_days: int = int(os.getenv("REFERRAL_BONUS_DAYS", "1"))

    crypto_pay_enabled: bool = flag("CRYPTO_PAY_ENABLED", True)
    crypto_pay_token: str = os.getenv("CRYPTO_PAY_TOKEN", "").strip()
    crypto_pay_network: str = os.getenv("CRYPTO_PAY_NETWORK", "testnet").lower()

    xrocket_enabled: bool = flag("XROCKET_ENABLED", False)
    xrocket_token: str = os.getenv("XROCKET_TOKEN", "").strip()
    xrocket_network: str = os.getenv("XROCKET_NETWORK", "testnet").lower()
    xrocket_asset: str = os.getenv("XROCKET_ASSET", "USDT").upper()

    web_admin_enabled: bool = flag("WEB_ADMIN_ENABLED", True)
    web_admin_username: str = os.getenv("WEB_ADMIN_USERNAME", "admin")
    web_admin_password: str = os.getenv("WEB_ADMIN_PASSWORD", "")
    port: int = int(os.getenv("PORT", "8080"))

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


settings = Settings()

if not settings.bot_token:
    raise RuntimeError("BOT_TOKEN не задан")
if settings.crypto_pay_enabled and not settings.crypto_pay_token:
    raise RuntimeError("CRYPTO_PAY_ENABLED=true, но CRYPTO_PAY_TOKEN не задан")
if settings.xrocket_enabled and not settings.xrocket_token:
    raise RuntimeError("XROCKET_ENABLED=true, но XROCKET_TOKEN не задан")
if settings.web_admin_enabled and not settings.web_admin_password:
    raise RuntimeError("WEB_ADMIN_ENABLED=true, но WEB_ADMIN_PASSWORD не задан")
