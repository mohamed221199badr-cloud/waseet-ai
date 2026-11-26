"""m011: official menu photo reference on businesses (UI v1.8 section 3.0).

Stores the Telegram file_id of the shop's menu photo (not the bytes: Telegram
serves the file), who uploaded it, and when. NULL = no menu image yet, so the
bot asks for one instead of sending it.
"""

import sqlalchemy as sa

from alembic import op

revision = "m011_menu_photo"
down_revision = "m009_order_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses",
                  sa.Column("menu_photo_file_id", sa.String(length=200), nullable=True))
    op.add_column("businesses",
                  sa.Column("menu_photo_uploaded_by", sa.BigInteger(), nullable=True))
    op.add_column("businesses",
                  sa.Column("menu_photo_uploaded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "menu_photo_uploaded_at")
    op.drop_column("businesses", "menu_photo_uploaded_by")
    op.drop_column("businesses", "menu_photo_file_id")
