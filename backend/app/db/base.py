"""所有 SQLAlchemy 数据表共享的声明基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """集中保存业务数据表的元数据。"""
