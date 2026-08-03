"""Data model: SQLAlchemy ORM tables + Pydantic validation schemas.

Two layers on purpose:
- Pydantic `*In` models validate/clean every record scraped from the site
  BEFORE it touches the DB (this is the "data validation" step Ashray flagged).
- SQLAlchemy models are the persisted shape in Postgres (schema=`totalwine`).

All prices/availability are store- and time-scoped, so variant prices and
availability keep a `captured_at` history rather than overwriting in place.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = None  # set below so every table lands in the configured schema


# Bind all tables to the configured schema (e.g. `totalwine`).
from sqlalchemy import MetaData  # noqa: E402

Base.metadata = MetaData(schema=config.db_schema)


# --------------------------------------------------------------------------- #
# ORM tables
# --------------------------------------------------------------------------- #
class Product(Base):
    __tablename__ = "product"

    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    brand: Mapped[str | None] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)
    subcategory: Mapped[str | None] = mapped_column(String)
    url: Mapped[str | None] = mapped_column(String)
    ai_review_summary: Mapped[str | None] = mapped_column(Text)
    avg_rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    variants: Mapped[list["ProductVariant"]] = relationship(back_populates="product")
    reviews: Mapped[list["Review"]] = relationship(back_populates="product")


class ProductVariant(Base):
    __tablename__ = "product_variant"

    variant_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"))
    size: Mapped[str | None] = mapped_column(String)
    price: Mapped[float | None] = mapped_column(Float)
    in_stock: Mapped[bool | None] = mapped_column(Boolean)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, primary_key=True
    )

    product: Mapped[Product] = relationship(back_populates="variants")


class Review(Base):
    __tablename__ = "review"

    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"))
    rating: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(String)
    body: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String)
    review_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    helpful_count: Mapped[int | None] = mapped_column(Integer)

    product: Mapped[Product] = relationship(back_populates="reviews")


class Store(Base):
    __tablename__ = "store"

    store_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    city: Mapped[str | None] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String)
    zip: Mapped[str | None] = mapped_column(String)


class ProductStoreAvailability(Base):
    __tablename__ = "product_store_availability"

    product_id: Mapped[str] = mapped_column(
        ForeignKey("product.product_id"), primary_key=True
    )
    store_id: Mapped[str] = mapped_column(ForeignKey("store.store_id"), primary_key=True)
    pickup_available: Mapped[bool | None] = mapped_column(Boolean)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, primary_key=True
    )


class ScrapeRun(Base):
    __tablename__ = "scrape_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_ingested: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- #
# Pydantic validation schemas (validate before persist)
# --------------------------------------------------------------------------- #
class ProductIn(BaseModel):
    product_id: str
    name: str
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    url: str | None = None
    ai_review_summary: str | None = None
    avg_rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product name is empty")
        return v


class VariantIn(BaseModel):
    variant_id: str
    product_id: str
    size: str | None = None
    price: float | None = Field(default=None, ge=0)
    in_stock: bool | None = None


class ReviewIn(BaseModel):
    review_id: str
    product_id: str
    rating: float | None = Field(default=None, ge=0, le=5)
    title: str | None = None
    body: str | None = None
    author: str | None = None
    review_date: datetime | None = None
    helpful_count: int | None = Field(default=None, ge=0)


class StoreIn(BaseModel):
    store_id: str
    name: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
