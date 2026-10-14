"""用于浏览和搜索商品的 FastAPI 路由。"""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.modules.auth.dependencies import AdminPrincipal
from app.modules.catalog.schemas import (
    Product,
    ProductCreate,
    ProductId,
    ProductListResponse,
    ProductSearchParams,
    ProductUpdate,
    StockAdjustment,
)
from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.catalog.repository import SqlProductRepository
from app.modules.catalog.service import (
    InsufficientStockError,
    ProductAlreadyExistsError,
    ProductNotFoundError,
    ProductService,
)

from app.modules.catalog.images import router as image_router

router = APIRouter(prefix="/products", tags=["products"])
router.include_router(image_router)


@lru_cache(maxsize=1)
def get_product_service() -> ProductService:
    """复用连接数据库的商品服务。"""

    initialize_database()
    repository = SqlProductRepository(get_session_factory())
    return ProductService(repository)


ProductServiceDependency = Annotated[ProductService, Depends(get_product_service)]


@router.get("", response_model=ProductListResponse, summary="查询商品列表")
async def list_products(
    filters: Annotated[ProductSearchParams, Query()],
    service: ProductServiceDependency,
) -> ProductListResponse:
    """根据关键词、分类、价格、库存、排序和分页条件查询商品。"""

    return service.search_products(filters)


@router.get("/categories", response_model=list[str], summary="查询商品分类")
async def list_categories(service: ProductServiceDependency) -> list[str]:
    """返回默认分类与当前商品使用的分类。"""
    categories = ["耳机", "充电器", "扩展坞"]
    categories.extend(product.category for product in service.repository.list_products())
    return list(dict.fromkeys(categories))


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


@router.post(
    "",
    response_model=Product,
    status_code=status.HTTP_201_CREATED,
    summary="新增商品",
)
async def create_product(
    payload: ProductCreate,
    _admin: AdminPrincipal,
    service: ProductServiceDependency,
) -> Product:
    """管理员新增一件商品，编号由系统自动分配。"""

    try:
        return service.create_product(payload)
    except ProductAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.patch(
    "/{product_id}/stock",
    response_model=Product,
    responses={status.HTTP_404_NOT_FOUND: {"description": "商品不存在"}},
    summary="调整商品库存",
)
async def adjust_product_stock(
    product_id: Annotated[ProductId, Path(description="商品编号，例如 p01")],
    payload: StockAdjustment,
    _admin: AdminPrincipal,
    service: ProductServiceDependency,
) -> Product:
    """管理员盘点（set）、入库（add）或出库（subtract）。"""

    try:
        return service.adjust_stock(product_id, payload)
    except ProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InsufficientStockError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.patch(
    "/{product_id}",
    response_model=Product,
    responses={status.HTTP_404_NOT_FOUND: {"description": "商品不存在"}},
    summary="更新商品信息",
)
async def update_product(
    product_id: Annotated[ProductId, Path(description="商品编号，例如 p01")],
    payload: ProductUpdate,
    _admin: AdminPrincipal,
    service: ProductServiceDependency,
) -> Product:
    """管理员部分更新商品字段。"""

    try:
        return service.update_product(product_id, payload)
    except ProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_404_NOT_FOUND: {"description": "商品不存在"}},
    summary="删除商品",
)
async def delete_product(
    product_id: Annotated[ProductId, Path(description="商品编号，例如 p01")],
    _admin: AdminPrincipal,
    service: ProductServiceDependency,
) -> None:
    """管理员删除一件商品。"""

    try:
        service.delete_product(product_id)
    except ProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
