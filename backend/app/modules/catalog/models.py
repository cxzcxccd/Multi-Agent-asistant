"""商品目录的 SQLAlchemy 持久化模型。"""

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductRecord(Base):
    """数据库中的一件商品。"""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    series: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)
    specs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
