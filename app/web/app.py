import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.core import bot
from app.config import settings
from app.database import connect, now_ts
from app.utils import parse_price

web_app = FastAPI(title="DT Team V2 Admin")
security = HTTPBasic()
templates = Jinja2Templates(directory="templates")


def auth(credentials: HTTPBasicCredentials = Depends(security)):
    valid_user = secrets.compare_digest(credentials.username, settings.web_admin_username)
    valid_pass = secrets.compare_digest(credentials.password, settings.web_admin_password)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@web_app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _: str = Depends(auth)):
    db = await connect()
    try:
        users = (await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone())["c"]
        products = (await (await db.execute("SELECT COUNT(*) c FROM products WHERE active=1")).fetchone())["c"]
        active = (await (await db.execute(
            "SELECT COUNT(*) c FROM users WHERE access_until>?", (now_ts(),)
        )).fetchone())["c"]
        rows = await (await db.execute(
            "SELECT * FROM products WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 50"
        )).fetchall()
    finally:
        await db.close()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "users": users,
        "products": products,
        "active": active,
        "rows": rows,
    })


@web_app.post("/products")
async def add_product(
    category: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    price: str = Form(...),
    image: UploadFile = File(...),
    _: str = Depends(auth),
):
    data = await image.read()
    sent = await bot.send_document(
        chat_id=next(iter(settings.admin_ids)),
        document=(image.filename or "image.jpg", data),
        caption="Изображение сохранено для DT Team V2",
    )
    file_id = sent.document.file_id
    db = await connect()
    try:
        await db.execute("""
            INSERT INTO products(category, title, description, image_file_id,
                                 image_type, price_text, price_num, active, created_at)
            VALUES (?, ?, ?, ?, 'document', ?, ?, 1, ?)
        """, (category, title, description, file_id, price, parse_price(price), now_ts()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/", status_code=303)


@web_app.post("/products/{product_id}/toggle")
async def toggle_product(product_id: int, _: str = Depends(auth)):
    db = await connect()
    try:
        await db.execute("""
            UPDATE products SET active=1-active
            WHERE id=? AND deleted_at IS NULL
        """, (product_id,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/", status_code=303)


@web_app.post("/products/{product_id}/delete")
async def delete_product(product_id: int, _: str = Depends(auth)):
    db = await connect()
    try:
        await db.execute(
            "UPDATE products SET deleted_at=?, active=0 WHERE id=?",
            (now_ts(), product_id),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/", status_code=303)


@web_app.get("/trash", response_class=HTMLResponse)
async def trash(request: Request, _: str = Depends(auth)):
    db = await connect()
    try:
        rows = await (await db.execute(
            "SELECT * FROM products WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        )).fetchall()
    finally:
        await db.close()
    return templates.TemplateResponse("trash.html", {
        "request": request,
        "rows": rows,
    })


@web_app.post("/products/{product_id}/restore")
async def restore_product(product_id: int, _: str = Depends(auth)):
    db = await connect()
    try:
        await db.execute(
            "UPDATE products SET deleted_at=NULL, active=1 WHERE id=?",
            (product_id,),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/trash", status_code=303)


@web_app.post("/products/{product_id}/hard-delete")
async def hard_delete_product(product_id: int, _: str = Depends(auth)):
    db = await connect()
    try:
        await db.execute(
            "DELETE FROM products WHERE id=? AND deleted_at IS NOT NULL",
            (product_id,),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/trash", status_code=303)


@web_app.get("/health")
async def health():
    return {"ok": True, "service": "DT Team V2"}
