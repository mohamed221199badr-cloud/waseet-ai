"""Migrations must go up and down cleanly on a scratch database."""

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ.get(
    "ADMIN_DATABASE_URL", "postgresql+psycopg2://postgres@localhost:5433/postgres"
)
SCRATCH = "waseet_mig_test"
EXPECTED = {"businesses", "users", "products", "product_aliases", "audit_events"}  # M1 core


@pytest.fixture()
def scratch_url():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH}"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH}"))
    url = make_url(ADMIN_URL).set(database=SCRATCH).render_as_string(hide_password=False)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH}"))
    admin.dispose()


def _alembic(url, *args):
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_upgrade_and_downgrade(scratch_url):
    up = _alembic(scratch_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    eng = create_engine(scratch_url)
    assert EXPECTED <= set(inspect(eng).get_table_names())

    down = _alembic(scratch_url, "downgrade", "base")
    assert down.returncode == 0, down.stderr
    remaining = set(inspect(eng).get_table_names()) - {"alembic_version"}
    assert remaining == set(), f"tables left after downgrade: {remaining}"
    eng.dispose()

    up2 = _alembic(scratch_url, "upgrade", "head")
    assert up2.returncode == 0, up2.stderr
