"""m013: one photo per menu item, bytes in the DB (his go 19677984, option 2).

telegram_file_id caches the id Telegram returns after the first upload so the
bot resends by reference instead of re-uploading bytes. One row per item.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "m013_item_images"
down_revision = "m012_autolink_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "menu_item_images",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("business_id", "item_id", name="uq_item_image"),
        sa.ForeignKeyConstraint(
            ["business_id", "item_id"], ["menu_items.business_id", "menu_items.id"],
            ondelete="CASCADE", name="fk_item_image_same_business"),
    )


def downgrade() -> None:
    op.drop_table("menu_item_images")
