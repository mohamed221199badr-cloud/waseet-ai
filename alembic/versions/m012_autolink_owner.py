"""m012: auto-link identity on customers + owner flag on telegram links.

customers.telegram_chat_id lets an unlinked chat be re-linked to the SAME
customer after /stop (order history and price overrides survive). Nullable:
dashboard-created customers have no chat until linked.
telegram_links.is_owner marks the shop owner's own chat; only owner links may
set the official menu photo (his answer 19673006). Set only through the
owner-authenticated API, never from a customer chat message.
"""

from alembic import op
import sqlalchemy as sa

revision = "m012_autolink_owner"
down_revision = "m011_menu_photo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers",
                  sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_customers_tg_chat", "customers",
                                ["business_id", "telegram_chat_id"])
    op.add_column("telegram_links",
                  sa.Column("is_owner", sa.Boolean(), nullable=False,
                            server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("telegram_links", "is_owner")
    op.drop_constraint("uq_customers_tg_chat", "customers", type_="unique")
    op.drop_column("customers", "telegram_chat_id")
