import html
from collections.abc import Sequence

from aiogram.types import InputMediaPhoto

from app.database import connect


async def get_product_images(product_id: int):
    db = await connect()
    try:
        return await (await db.execute(
            """
            SELECT file_id, image_type, position
            FROM product_images
            WHERE product_id=?
            ORDER BY position, id
            """,
            (product_id,),
        )).fetchall()
    finally:
        await db.close()


async def save_product_images(product_id: int, images: Sequence[dict], replace: bool = False) -> None:
    if not 1 <= len(images) <= 6:
        raise ValueError("У товара должно быть от 1 до 6 изображений")
    db = await connect()
    try:
        if replace:
            await db.execute("DELETE FROM product_images WHERE product_id=?", (product_id,))
        start_row = await (await db.execute(
            "SELECT COALESCE(MAX(position), -1) AS p FROM product_images WHERE product_id=?",
            (product_id,),
        )).fetchone()
        start = int(start_row["p"]) + 1
        for offset, image in enumerate(images):
            await db.execute(
                """
                INSERT INTO product_images(product_id, file_id, image_type, position, created_at)
                VALUES (?, ?, ?, ?, strftime('%s','now'))
                """,
                (product_id, image["file_id"], image.get("image_type", "photo"), start + offset),
            )
        await db.commit()
    finally:
        await db.close()


async def send_product_gallery(bot, chat_id: int, product_id: int) -> int:
    """Показывает все изображения товара. Возвращает их количество."""
    images = await get_product_images(product_id)
    if not images:
        return 0

    photos = [row for row in images if row["image_type"] == "photo"]
    documents = [row for row in images if row["image_type"] != "photo"]

    if len(photos) == 1:
        await bot.send_photo(chat_id, photos[0]["file_id"])
    elif photos:
        await bot.send_media_group(
            chat_id,
            [InputMediaPhoto(media=row["file_id"]) for row in photos[:6]],
        )

    # Изображения, отправленные как файлы, Telegram не объединяет с фото в один альбом.
    for row in documents:
        await bot.send_document(chat_id, row["file_id"])
    return len(images)
