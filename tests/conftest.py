"""Test fixtures: real PostgreSQL (waseet_test), migrated once, truncated per test."""

import os
import uuid

import pytest
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command as alembic_command

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg2://postgres@localhost:5433/waseet_test"
)

TABLES = ["audit_events", "product_aliases", "products", "users", "businesses"]


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_URL)
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    alembic_command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    for table in TABLES:
        session.execute(text(f'TRUNCATE TABLE "{table}" CASCADE'))
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    from app.db import get_db
    from app.main import app

    def override():
        yield db_session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def two_businesses(db_session):
    """Two synthetic tenants, one product each. Returns dict of ids."""
    from app.models import Business, Product, User

    biz_a = Business(name="شركة أ تجريبية", slug="test-biz-a")
    biz_b = Business(name="شركة ب تجريبية", slug="test-biz-b")
    db_session.add_all([biz_a, biz_b])
    db_session.flush()
    db_session.add_all(
        [
            User(business_id=biz_a.id, full_name="مالك أ", email="a@test.local"),
            User(business_id=biz_b.id, full_name="مالك ب", email="b@test.local"),
            Product(
                business_id=biz_a.id, sku="A-001", name="منتج أ", unit="piece",
                price="10.00", stock_quantity=5,
            ),
            Product(
                business_id=biz_b.id, sku="B-001", name="منتج ب", unit="piece",
                price="20.00", stock_quantity=7,
            ),
        ]
    )
    db_session.commit()
    product_a = db_session.query(Product).filter_by(business_id=biz_a.id).one()
    return {
        "a": biz_a.id,
        "b": biz_b.id,
        "product_a": product_a.id,
        "nonexistent": uuid.uuid4(),
    }
