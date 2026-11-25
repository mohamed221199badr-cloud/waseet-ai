"""Phase 1 gates (task 1.8): B2 boundary, permission matrix, m006-m008 up/down."""

import ast
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.auth import create_access_token
from app.models import User
from app.permissions import ALL_ROLES, Permission, role_has
from tests.conftest import test_password_hash
from tests.test_chat_agent import linked, shop  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401
from tests.test_telegram import say

REPO = Path(__file__).resolve().parent.parent

# Modules the bot may never touch in Phase 1 (B2: the bot reads `products` only).
FORBIDDEN_BOT_IMPORTS = ("app.models_menu", "app.repositories.menu",
                         "app.menu_import", "app.price_service", "app.staff_service")


def test_bot_import_boundary():
    """Static boundary: app/chat and app/llm import nothing from the menu stack,
    so the B2 boundary cannot erode silently."""
    offenders = []
    for pkg in ("app/chat", "app/llm"):
        for file in (REPO / pkg).rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for mod in names:
                    if any(mod == f or mod.startswith(f + ".")
                           for f in FORBIDDEN_BOT_IMPORTS):
                        offenders.append(f"{file}: {mod}")
    assert offenders == []


def test_menu_edit_does_not_change_bot_reply(db_session, linked):  # noqa: F811
    """B2 acceptance: changing a menu price and availability never changes what the
    bot quotes - it still reads `products` until the Phase 2 cutover."""
    before = say(db_session, 500, "٣ سكر")
    assert "3 × سكر 1 كيلو" in before
    say(db_session, 500, "تمام")  # close the session
    # now edit the NEW menu: same name as the product, different price, unavailable
    from app.models_menu import MenuCategory, MenuItem, MenuItemVariant
    biz = db_session.scalar(sa.select(User).where(User.role == "owner")).business_id
    db_session.add(MenuCategory(business_id=biz, name="اللحوم"))
    db_session.flush()
    item = MenuItem(business_id=biz, category="اللحوم", name="سكر 1 كيلو",
                    sold_by="unit", is_available=False)
    db_session.add(item)
    db_session.flush()
    db_session.add(MenuItemVariant(business_id=biz, item_id=item.id, name="سعر 1",
                                   price="999.00", is_default=True))
    db_session.commit()
    after = say(db_session, 500, "٣ سكر")
    assert after == before


@pytest.mark.parametrize("role", ALL_ROLES)
def test_permission_matrix(role, client, db_session, two_businesses):
    """Every role gets exactly the menu/staff access its permissions say (F1)."""
    biz = two_businesses["a"]
    u = User(business_id=biz, full_name="x", email=f"matrix-{role}@test.local",
             role=role, password_hash=test_password_hash(),
             is_active=(role != "system"))
    db_session.add(u)
    db_session.commit()
    tok, _ = create_access_token(u.id, u.business_id)
    h = {"Authorization": f"Bearer {tok}"}

    def allowed(resp):
        return resp.status_code == 200  # 403 forbidden, 401 deactivated/unauthenticated

    assert allowed(client.get("/api/v1/menu/items", headers=h)) == \
        role_has(role, Permission.MENU_MANAGE)
    assert allowed(client.get("/api/v1/products", headers=h)) == \
        role_has(role, Permission.MENU_MANAGE)
    assert allowed(client.get("/api/v1/staff", headers=h)) == (
        role_has(role, Permission.STAFF_MANAGE)
        or role_has(role, Permission.STAFF_MANAGE_LIMITED))


def test_migrations_m006_to_m008_up_down_up(scratch_url):  # noqa: F811
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    assert _alembic(scratch_url, "downgrade", "m5_0001").returncode == 0
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
