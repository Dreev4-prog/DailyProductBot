# DT Team V2.1 Stable

Единая стабильная версия без отдельных патчей.

## Возможности

Пользователь:
- современное inline-меню;
- `/start`;
- 2 персональных товара ежедневно;
- отсутствие повторов;
- профиль, архив и избранное;
- отметки по товару;
- Crypto Bot и xRocket;
- автоматическая активация доступа.

Администратор:
- `/admin`;
- добавление товара;
- просмотр базы;
- редактирование категории, названия, описания, изображения и цены;
- скрытие и включение;
- корзина, восстановление и удаление навсегда;
- пользователи, аналитика и рассылка;
- `/paymentstatus`.

Веб-панель:
- добавление товара;
- управление активностью;
- корзина;
- восстановление и окончательное удаление;
- статистика.

## Railway Variables

```text
BOT_TOKEN=...
ADMIN_IDS=123456789
DATABASE_PATH=/app/data/dt_team_v2.db

WEB_ADMIN_ENABLED=true
WEB_ADMIN_USERNAME=admin
WEB_ADMIN_PASSWORD=СЛОЖНЫЙ_ПАРОЛЬ

CRYPTO_PAY_ENABLED=true
CRYPTO_PAY_TOKEN=...
CRYPTO_PAY_NETWORK=mainnet

XROCKET_ENABLED=true
XROCKET_TOKEN=...
XROCKET_NETWORK=mainnet
XROCKET_ASSET=USDT

PRICE_USDT=20
ACCESS_DAYS=5
PRODUCTS_PER_DAY=2
TIMEZONE=Europe/Rome
SEND_HOUR=10
SEND_MINUTE=0
```

Если xRocket пока не настроен:

```text
XROCKET_ENABLED=false
```

## Railway Start Command

```text
python main.py
```

## Установка через веб-GitHub

1. Распакуйте архив.
2. Откройте репозиторий.
3. Нажмите `Add file → Upload files`.
4. Загрузите всё содержимое папки с заменой.
5. Нажмите `Commit changes`.
6. Дождитесь завершения всей очереди Railway Deployments.
7. Проверьте `/start`, `/admin`, `/paymentstatus`.

Не загружайте старые патчи поверх этой версии.

## Railway Volume

```text
/app/data
```
