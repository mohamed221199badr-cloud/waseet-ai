"""m014: per-category (and per-item) menu photos (WhatsApp spec 2026-11-26).

The owner uploads one photo per menu category from his owner chat (photo with the
category name as caption); the bot shows it, with the items as buttons under it,
when a customer opens that category. Telegram file_id references only, no bytes
stored - same pattern as businesses.menu_photo_file_id. menu_items.photo_file_id
ships in the same migration for the item-photos phase; v1 uses categories only.
Nullable: categories/items without a photo render exactly as before.
"""

from alembic import op
import sqlalchemy as sa

revision = "m014_category_photos"
down_revision = "m013_delivery_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("menu_categories",
                  sa.Column("photo_file_id", sa.String(length=200), nullable=True))
    op.add_column("menu_items",
                  sa.Column("photo_file_id", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("menu_items", "photo_file_id")
    op.drop_column("menu_categories", "photo_file_id")
