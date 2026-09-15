"""用于浏览和搜索商品的 FastAPI 路由。"""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.modules.catalog.schemas import (
    Product,
    ProductId,
    ProductListResponse,
    ProductSearchParams,
)
from app.modules.catalog.service import ProductNotFoundError, ProductService

router = APIRouter(prefix="/products", tags=["products"])


@lru_cache(maxsize=1)
def get_product_service() -> ProductService:
    """复用商品服务及其已校验的数据缓存。"""

    return ProductService()


ProductServiceDependency = Annotated[ProductService, Depends(get_product_service)]


@router.get("", response_model=ProductListResponse, summary="查询商品列表")
async def list_products(
    filters: Annotated[ProductSearchParams, Query()],
    service: ProductServiceDependency,
) -> ProductListResponse:
    """根据关键词、分类、价格、库存、排序和分页条件查询商品。"""

    return service.search_products(filters)


@router.get(
    "/{product_id}",
    response_model=Product,
    responses={status.HTTP_404_NOT_FOUND: {"description": "商品不存在"}},
    summary="查询商品详情",
)
async def get_product(
    product_id: Annotated[ProductId, Path(description="商品编号，例如 p01")],
    service: ProductServiceDependency,
) -> Product:
    """按商品编号查询详情。"""

    try:
        return service.get_product(product_id)
    except ProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
