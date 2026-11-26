"""m010_orders: the full order flow (Phase 3, v3 B5/B6/C2).

Data-safe status rename: 'pending' becomes 'awaiting_confirmation' (UPDATE first,
then the CHECK swap, so no row ever violates the constraint). New statuses complete
the kitchen/delivery flow: cancelled, preparing, ready, out_for_delivery, delivered,
failed_delivery.

orders grows the B6 columns: delivery_attempts, payment_status, confirmed_total,
requires_reconfirmation, assigned_driver_id.

order_status_history records every transition with actor attribution (C2):
actor_type 'user' requires actor_id; 'system'/'customer' rows carry NULL.

Revision ID: m010_orders
Revises: m009_order_lines
"""

import sqlalchemy as sa

from alembic import op

revision = "m010_orders"
down_revision = "m009_order_lines"
branch_labels = None
depends_on = None

OLD_STATUSES = "('draft','pending','confirmed','rejected')"
NEW_STATUSES = ("('draft','awaiting_confirmation','confirmed','rejected','cancelled',"
                "'preparing','ready','out_for_delivery','delivered','failed_delivery')")


def upgrade() -> None:
    # 'awaiting_confirmation' (21 chars) does not fit the old VARCHAR(20)
    op.alter_column("orders", "status", type_=sa.String(length=30))
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.execute("UPDATE orders SET status = 'awaiting_confirmation' WHERE status = 'pending'")
    op.create_check_constraint("ck_orders_status", "orders", f"status in {NEW_STATUSES}")

    op.add_column("orders", sa.Column("delivery_attempts", sa.Integer(),
                                      nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("payment_status", sa.String(length=20),
                                      nullable=False, server_default="pending"))
    op.add_column("orders", sa.Column("confirmed_total", sa.Numeric(14, 2), nullable=True))
    op.add_column("orders", sa.Column("requires_reconfirmation", sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
    op.add_column("orders", sa.Column("assigned_driver_id", sa.UUID(), nullable=True))
    op.create_check_constraint(
        "ck_orders_payment_status", "orders",
        "payment_status in ('pending','collected','refunded')")
    op.create_foreign_key(
        "fk_order_driver", "orders", "users", ["assigned_driver_id"], ["id"])

    op.create_table(
        "order_status_history",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("actor_type", sa.String(length=10), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("actor_type in ('user','system','customer')",
                           name="ck_osh_actor_type"),
        sa.CheckConstraint("actor_type <> 'user' or actor_id is not null",
                           name="ck_osh_named_actor"),
        sa.CheckConstraint("actor_type = 'user' or actor_id is null",
                           name="ck_osh_customer_unattributed"),
        sa.ForeignKeyConstraint(
            ["business_id", "order_id"], ["orders.business_id", "orders.id"],
            ondelete="CASCADE", name="fk_osh_order_same_business"),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE", name="fk_osh_business"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name="fk_osh_actor"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_osh_order", "order_status_history", ["business_id", "order_id"])


def downgrade() -> None:
    op.drop_index("ix_osh_order", table_name="order_status_history")
    op.drop_table("order_status_history")

    op.drop_constraint("fk_order_driver", "orders", type_="foreignkey")
    op.drop_constraint("ck_orders_payment_status", "orders", type_="check")
    op.drop_column("orders", "assigned_driver_id")
    op.drop_column("orders", "requires_reconfirmation")
    op.drop_column("orders", "confirmed_total")
    op.drop_column("orders", "payment_status")
    op.drop_column("orders", "delivery_attempts")

    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.execute("UPDATE orders SET status = 'pending' WHERE status = 'awaiting_confirmation'")
    op.create_check_constraint("ck_orders_status", "orders", f"status in {OLD_STATUSES}")
    op.alter_column("orders", "status", type_=sa.String(length=20))
