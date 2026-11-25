"""Milestone 2 migration: new tables appear on upgrade, only Milestone 1 tables remain
after stepping back one revision, and the step can be re-applied."""

from sqlalchemy import create_engine, inspect

from tests.test_migrations import ADMIN_URL, _alembic, scratch_url  # noqa: F401

M2 = {"customers", "customer_prices", "inventory_movements", "orders", "order_items"}
M1 = {"businesses", "users", "products", "product_aliases", "audit_events"}


def test_m2_step_down_and_up(scratch_url):  # noqa: F811
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng = create_engine(scratch_url)
    assert M2 <= set(inspect(eng).get_table_names())
    down = _alembic(scratch_url, "downgrade", "a85430c218dd")
    assert down.returncode == 0, down.stderr
    tables = set(inspect(eng).get_table_names()) - {"alembic_version"}
    assert tables == M1
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng.dispose()
