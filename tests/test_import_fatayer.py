"""scripts/import_fatayer.py: idempotent, touches only الفطائر."""

import sys

from sqlalchemy import select

from app.models_menu import MenuCategory, MenuItem, MenuItemVariant
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import menu  # noqa: F401
from tests.test_telegram import issue  # noqa: F401


def test_import_is_idempotent_and_scoped(db_session, menu):  # noqa: F811
    sys.path.insert(0, "scripts")
    import import_fatayer as imp

    from app.models import Business
    biz = db_session.scalar(select(Business))
    before_cats = {c.name for c in db_session.scalars(select(MenuCategory)).all()}
    a = imp.run(db_session, biz.id, commit=True)
    b = imp.run(db_session, biz.id, commit=True)
    assert a == b and a["items"] == len(imp.ITEMS) - 6
    after = {c.name for c in db_session.scalars(select(MenuCategory)).all()}
    assert after - before_cats == {"الفطائر"}
    n = db_session.scalars(select(MenuItemVariant).join(
        MenuItem, MenuItem.id == MenuItemVariant.item_id).where(
        MenuItem.category == "الفطائر")).all()
    assert len(n) == a["variants"]
    assert db_session.scalar(select(MenuItem).where(
        MenuItem.category == "اللحوم")) is not None  # untouched
