"""管理员上传商品图片，公共接口读取图片。"""

import re
import warnings
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from app.modules.auth.dependencies import AdminPrincipal

router = APIRouter()
IMAGE_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "product_images"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


@router.post("/images", status_code=201, summary="上传商品图片")
async def upload_image(request: Request, _admin: AdminPrincipal) -> dict[str, str]:
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "图片不能超过 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(chunks)) as image:
                extension = FORMATS.get(image.format)
                if not extension:
                    raise HTTPException(422, "请选择 JPG、PNG 或 WebP 图片")
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "图片损坏或格式不支持，请选择有效的 JPG、PNG 或 WebP 图片") from exc
    IMAGE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.{extension}"
    (IMAGE_DIRECTORY / filename).write_bytes(chunks)
    return {"image_url": f"/api/products/images/{filename}"}


@router.get("/images/{filename}", summary="读取商品图片")
async def get_image(filename: str) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp)", filename):
        raise HTTPException(404, "图片不存在")
    path = IMAGE_DIRECTORY / filename
    if not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})
