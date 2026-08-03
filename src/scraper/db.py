"""Database engine, session, schema bootstrap, and idempotent upserts.

Upserts are keyed on natural ids so re-running a scrape updates existing rows
instead of duplicating them (needed for the quarterly cadence).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from .config import config
from .models import (
    Base,
    Product,
    ProductStoreAvailability,
    ProductVariant,
    Review,
    Store,
)

engine = create_engine(config.db_url, future=True)
SessionLocal = sessionmaker(bind=engine, future=True)


def init_db() -> None:
    """Create the schema and all tables if they don't exist."""
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{config.db_schema}"'))
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upsert_products(session: Session, rows: list[dict]) -> int:
    """Upsert products on product_id; refresh mutable fields + last_seen."""
    if not rows:
        return 0
    stmt = pg_insert(Product).values(rows)
    update_cols = {
        c: stmt.excluded[c]
        for c in (
            "name",
            "brand",
            "category",
            "subcategory",
            "url",
            "ai_review_summary",
            "avg_rating",
            "review_count",
            "last_seen",
        )
        if c in rows[0]
    }
    stmt = stmt.on_conflict_do_update(index_elements=["product_id"], set_=update_cols)
    session.execute(stmt)
    return len(rows)


def insert_variants(session: Session, rows: list[dict]) -> int:
    """Variants are time-scoped (price history); ignore exact-duplicate snapshots."""
    if not rows:
        return 0
    stmt = pg_insert(ProductVariant).values(rows).on_conflict_do_nothing(
        index_elements=["variant_id", "captured_at"]
    )
    session.execute(stmt)
    return len(rows)


def upsert_reviews(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(Review).values(rows).on_conflict_do_nothing(
        index_elements=["review_id"]
    )
    session.execute(stmt)
    return len(rows)


def upsert_stores(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(Store).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["store_id"],
        set_={c: stmt.excluded[c] for c in ("name", "city", "state", "zip")},
    )
    session.execute(stmt)
    return len(rows)


def insert_availability(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(ProductStoreAvailability).values(rows).on_conflict_do_nothing(
        index_elements=["product_id", "store_id", "captured_at"]
    )
    session.execute(stmt)
    return len(rows)
