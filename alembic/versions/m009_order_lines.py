"""m009_order_lines: order_lines, the menu-based evolution of order_items (Phase 2 cutover).

Additive only: order_items, the bot's legacy path, the dashboard and the migration
downgrade are all untouched. A line freezes item/variant names, unit and price at order
time, so later menu edits never rewrite history. price_source follows B6: 'menu' and
'override' come from rules (priced_by/at stay NULL), 'cashier_manual' requires both.

Revision ID: m009_order_lines
Revises: m008_exceptions
"""

import sqlalchemy as sa

from alembic import op

revision = "m009_order_lines"
down_revision = "m008_exceptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_lines",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("item_name", sa.String(length=300), nullable=False),
        sa.Column("variant_name", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("unit_label", sa.String(length=30), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_source", sa.String(length=20), nullable=False),
        sa.Column("priced_by", sa.UUID(), nullable=True),
        sa.Column("priced_at", sa.DateTime(), nullable=True),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_line_qty_positive"),
        sa.CheckConstraint(
            "price_source in ('menu','override','cashier_manual')", name="ck_line_price_source"
        ),
        sa.CheckConstraint(
            "price_source = 'cashier_manual' or (priced_by is null and priced_at is null)",
            name="ck_line_rule_price_unattributed",
        ),
        sa.CheckConstraint(
            "price_source <> 'cashier_manual' or (priced_by is not null and priced_at is not null)",
            name="ck_line_manual_price_attributed",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "order_id"], ["orders.business_id", "orders.id"],
            ondelete="CASCADE", name="fk_line_order_same_business",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "variant_id"],
            ["menu_item_variants.business_id", "menu_item_variants.id"],
            name="fk_line_variant_same_business",
        ),
        sa.ForeignKeyConstraint(["priced_by"], ["users.id"], name="fk_line_priced_by"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "id", name="uq_order_lines_business_id"),
        sa.UniqueConstraint("order_id", "variant_id", name="uq_order_lines_order_variant"),
    )
    op.create_index("ix_order_lines_business_id", "order_lines", ["business_id"])
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"])
    op.create_index("ix_order_lines_variant_id", "order_lines", ["variant_id"])


def downgrade() -> None:
    op.drop_table("order_lines")
