"""phase 1 order_exceptions extension (B3): fixed reason enum, resolution audit trail

Revision ID: m008_exceptions
Revises: m007_menu
Create Date: 2026-11-26 01:10:00
"""
import uuid

import sqlalchemy as sa

from alembic import op

revision = 'm008_exceptions'
down_revision = 'm007_menu'
branch_labels = None
depends_on = None

NEW_REASONS = ('price_required', 'unclear_item', 'special_request', 'complaint',
               'low_confidence', 'other')


def _system_user_id(business_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(business_id, "system")


def upgrade() -> None:
    # order_exceptions.order_line_id points at order_items through the tenant pattern.
    op.create_unique_constraint('uq_order_items_business_id', 'order_items', ['business_id', 'id'])

    op.alter_column('order_exceptions', 'decided_at', new_column_name='resolved_at')
    op.add_column('order_exceptions', sa.Column('reason_detail', sa.String(length=30), nullable=True))
    op.add_column('order_exceptions', sa.Column('order_line_id', sa.UUID(), nullable=True))
    op.add_column('order_exceptions', sa.Column('customer_message', sa.Text(), nullable=True))
    op.add_column('order_exceptions', sa.Column('resolution_note', sa.Text(), nullable=True))
    op.add_column('order_exceptions', sa.Column('resolved_by', sa.UUID(), nullable=True))

    # Legacy matcher codes move to reason_detail; the enum value becomes unclear_item.
    op.execute(
        "update order_exceptions set reason_detail = reason, reason = 'unclear_item' "
        "where reason not in (" + ",".join(f"'{r}'" for r in NEW_REASONS) + ")"
    )

    # Rows already closed before this migration have no recorded actor: attribute them to
    # the business's system actor so the new "closed rows must name who and when" rule holds.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "select distinct business_id from order_exceptions where status <> 'open'"
    ))
    for (biz_id,) in rows:
        bind.execute(
            sa.text("update order_exceptions set resolved_by = :uid "
                    "where business_id = :bid and status <> 'open' and resolved_by is null"),
            {"uid": _system_user_id(biz_id), "bid": biz_id},
        )

    # Legacy dismissed rows predate the note requirement; give them a migration note so
    # the new dismiss-note rule can hold without rewriting history semantics.
    op.execute(
        "update order_exceptions set resolution_note = 'dismissed before Phase 1' "
        "where status = 'dismissed' and (resolution_note is null or btrim(resolution_note, ' ') = '')"
    )

    op.create_check_constraint(
        'ck_order_exceptions_reason', 'order_exceptions',
        "reason in (" + ",".join(f"'{r}'" for r in NEW_REASONS) + ")",
    )
    op.create_check_constraint(
        'ck_order_exceptions_resolution', 'order_exceptions',
        "(status = 'open' and resolved_by is null and resolved_at is null) or "
        "(status <> 'open' and resolved_by is not null and resolved_at is not null)",
    )
    op.create_check_constraint(
        'ck_order_exceptions_dismiss_note', 'order_exceptions',
        "status <> 'dismissed' or length(btrim(resolution_note, ' ')) > 0",
    )
    op.create_foreign_key(
        'fk_exception_line_same_business', 'order_exceptions', 'order_items',
        ['business_id', 'order_line_id'], ['business_id', 'id'], ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_exception_resolver_same_business', 'order_exceptions', 'users',
        ['business_id', 'resolved_by'], ['business_id', 'id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_exception_resolver_same_business', 'order_exceptions', type_='foreignkey')
    op.drop_constraint('fk_exception_line_same_business', 'order_exceptions', type_='foreignkey')
    op.drop_constraint('ck_order_exceptions_dismiss_note', 'order_exceptions', type_='check')
    op.drop_constraint('ck_order_exceptions_resolution', 'order_exceptions', type_='check')
    op.drop_constraint('ck_order_exceptions_reason', 'order_exceptions', type_='check')
    # Restore the legacy matcher code as the reason where we moved it aside.
    op.execute("update order_exceptions set reason = reason_detail "
               "where reason_detail is not null")
    op.drop_column('order_exceptions', 'resolved_by')
    op.drop_column('order_exceptions', 'resolution_note')
    op.drop_column('order_exceptions', 'customer_message')
    op.drop_column('order_exceptions', 'order_line_id')
    op.drop_column('order_exceptions', 'reason_detail')
    op.alter_column('order_exceptions', 'resolved_at', new_column_name='decided_at')
    op.drop_constraint('uq_order_items_business_id', 'order_items', type_='unique')
