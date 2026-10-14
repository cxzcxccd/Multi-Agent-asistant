"""商品图片上传、权限及持久化回归测试。"""

import asyncio
from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.main import app
from app.modules.catalog import images
from tests.test_catalog_admin_api import ADMIN_HEADERS, BUYER_HEADERS, NEW_PRODUCT, api_request, product_service


def image_request(method, path, data=b"", headers=None):
    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, content=data, headers=headers)
    return asyncio.run(request())


@pytest.fixture
def image_directory(tmp_path, monkeypatch):
    directory = tmp_path / "images"
    monkeypatch.setattr(images, "IMAGE_DIRECTORY", directory)
    return directory


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (10, 10), "blue").save(stream, format="PNG")
    return stream.getvalue()


def test_upload_save_read_replace_and_remove_image(image_directory, product_service):
    raw = png_bytes()
    response = image_request("POST", "/api/products/images", raw, ADMIN_HEADERS)
    assert response.status_code == 201
    path = response.json()["image_url"]
    read = image_request("GET", path)
    assert read.status_code == 200
    assert read.content == raw
    assert read.headers["content-type"] == "image/png"
    created = api_request("POST", "/api/products", ADMIN_HEADERS, {**NEW_PRODUCT, "image_url": path})
    assert created.status_code == 201
    product_id = created.json()["id"]
    assert product_service.get_product(product_id).image_url == path
    other = image_request("POST", "/api/products/images", raw, ADMIN_HEADERS).json()["image_url"]
    assert api_request("PATCH", f"/api/products/{product_id}", ADMIN_HEADERS, {"image_url": other}).status_code == 200
    assert product_service.get_product(product_id).image_url == other
    assert api_request("PATCH", f"/api/products/{product_id}", ADMIN_HEADERS, {"image_url": ""}).status_code == 200
    assert product_service.get_product(product_id).image_url == ""


def test_upload_requires_admin(image_directory):
    assert image_request("POST", "/api/products/images", png_bytes()).status_code == 401
    assert image_request("POST", "/api/products/images", png_bytes(), BUYER_HEADERS).status_code == 403
    assert not image_directory.exists()


@pytest.mark.parametrize("raw", [b"", b"not an image", b"<svg></svg>", b"\x89PNG\r\n\x1a\ninvalid"])
def test_reject_invalid_image(raw, image_directory):
    assert image_request("POST", "/api/products/images", raw, ADMIN_HEADERS).status_code == 422
    assert not image_directory.exists()


def test_reject_large_image(image_directory):
    assert image_request("POST", "/api/products/images", b"x" * (images.MAX_IMAGE_BYTES + 1), ADMIN_HEADERS).status_code == 413
    assert not image_directory.exists()


def test_image_path_cannot_read_arbitrary_files(image_directory):
    assert image_request("GET", "/api/products/images/not-a-product.png").status_code == 404


def test_product_rejects_external_image_url(product_service):
    response = api_request("POST", "/api/products", ADMIN_HEADERS, {**NEW_PRODUCT, "image_url": "https://example.com/image.png"})
    assert response.status_code == 422
