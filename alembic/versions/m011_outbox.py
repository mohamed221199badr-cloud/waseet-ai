"""m011: notification outbox (Phase 3b, v3 C1).

One row per intended notification. dedupe_key is UNIQUE: two racing dispatchers
build the same key (build_dedupe_key is the only maker) and the second insert is
rejected. occurrence is the order_status_history.id that caused the event - NULL
for events that cannot repeat - so a repeated order.ready after redelivery is a
new row, not a swallowed duplicate. Failures stay visible (status/last_error)
from Phase 3 onward (C3).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "m011_outbox"
down_revision = "m010_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # the occurrence FK is composite (business_id, id) like every tenant-scoped FK
    op.create_unique_constraint("uq_osh_business_id", "order_status_history",
                                ["business_id", "id"])
    op.create_table(
        "notification_outbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("dedupe_key", sa.String(length=300), nullable=False, unique=True),
        sa.Column("event", sa.String(length=50), nullable=False),
        sa.Column("order_id", UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_type", sa.String(length=20), nullable=False),
        sa.Column("recipient_id", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False,
                  server_default="telegram"),
        sa.Column("occurrence", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("recipient_type in ('customer','user','role_group')",
                           name="ck_outbox_recipient_type"),
        sa.CheckConstraint("status in ('pending','sent','failed')",
                           name="ck_outbox_status"),
        sa.ForeignKeyConstraint(
            ["business_id", "order_id"], ["orders.business_id", "orders.id"],
            ondelete="CASCADE", name="fk_outbox_order_same_business"),
        sa.ForeignKeyConstraint(
            ["business_id", "occurrence"],
            ["order_status_history.business_id", "order_status_history.id"],
            ondelete="SET NULL", name="fk_outbox_occurrence_same_business"),
    )
    op.create_index("ix_outbox_pending", "notification_outbox",
                    ["business_id", "status"])


def downgrade() -> None:
    op.drop_table("notification_outbox")
    op.drop_constraint("uq_osh_business_id", "order_status_history", type_="unique")
