"""phase 1 menu tables (data-entry only until the Phase 2 cutover)

Revision ID: m007_menu
Revises: m006_roles
Create Date: 2026-11-26 01:05:00
"""
import sqlalchemy as sa

from alembic import op

revision = 'm007_menu'
down_revision = 'm006_roles'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'menu_categories',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_id', 'name', name='uq_menu_categories_name'),
    )
    op.create_index(op.f('ix_menu_categories_business_id'), 'menu_categories', ['business_id'])

    op.create_table(
        'menu_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('category', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('sold_by', sa.String(length=10), nullable=False),
        sa.Column('unit_label', sa.String(length=30), nullable=True),
        sa.Column('min_quantity', sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_available', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind in ('food','service')", name='ck_menu_items_kind'),
        sa.CheckConstraint("sold_by in ('unit','weight')", name='ck_menu_items_sold_by'),
        sa.CheckConstraint("sold_by <> 'weight' or unit_label is not null",
                           name='ck_menu_items_weight_label'),
        sa.CheckConstraint("min_quantity is null or min_quantity > 0",
                           name='ck_menu_items_min_qty'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_id', 'category'],
                                ['menu_categories.business_id', 'menu_categories.name'],
                                name='fk_menu_item_category_same_business'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_id', 'id', name='uq_menu_items_business_id'),
        sa.UniqueConstraint('business_id', 'category', 'name', name='uq_menu_items_natural'),
    )
    op.create_index(op.f('ix_menu_items_business_id'), 'menu_items', ['business_id'])

    op.create_table(
        'menu_item_variants',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('item_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('price is null or price >= 0', name='ck_variant_price_nonneg'),
        sa.ForeignKeyConstraint(['business_id', 'item_id'],
                                ['menu_items.business_id', 'menu_items.id'],
                                name='fk_variant_item_same_business', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_id', 'id', name='uq_variants_business_id'),
        sa.UniqueConstraint('business_id', 'item_id', 'name', name='uq_variants_natural'),
    )
    op.create_index(op.f('ix_menu_item_variants_business_id'), 'menu_item_variants', ['business_id'])
    op.create_index(op.f('ix_menu_item_variants_item_id'), 'menu_item_variants', ['item_id'])
    # Exactly one default variant per item (partial unique index).
    op.create_index('uq_variant_one_default', 'menu_item_variants', ['business_id', 'item_id'],
                    unique=True, postgresql_where=sa.text('is_default'))

    op.create_table(
        'menu_item_aliases',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('item_id', sa.UUID(), nullable=False),
        sa.Column('alias', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id', 'item_id'],
                                ['menu_items.business_id', 'menu_items.id'],
                                name='fk_menualias_item_same_business', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_id', 'alias', name='uq_menu_aliases_business_alias'),
    )
    op.create_index(op.f('ix_menu_item_aliases_business_id'), 'menu_item_aliases', ['business_id'])

    op.create_table(
        'price_overrides',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('variant_id', sa.UUID(), nullable=False),
        sa.Column('scope', sa.String(length=10), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=True),
        sa.Column('segment', sa.String(length=100), nullable=True),
        sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('starts_at', sa.DateTime(), nullable=True),
        sa.Column('ends_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("scope in ('customer','segment')", name='ck_override_scope'),
        sa.CheckConstraint('price >= 0', name='ck_override_price_nonneg'),
        sa.CheckConstraint("(customer_id is null) <> (segment is null)",
                           name='ck_override_one_target'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_id', 'variant_id'],
                                ['menu_item_variants.business_id', 'menu_item_variants.id'],
                                name='fk_override_variant_same_business', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_id', 'customer_id'],
                                ['customers.business_id', 'customers.id'],
                                name='fk_override_customer_same_business', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_price_overrides_business_id'), 'price_overrides', ['business_id'])
    op.create_index(op.f('ix_price_overrides_variant_id'), 'price_overrides', ['variant_id'])

    op.create_table(
        'restaurant_settings',
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('reconfirm_pct', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('reconfirm_abs_egp', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('refund_limit_egp', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('business_id'),
    )


def downgrade() -> None:
    op.drop_table('restaurant_settings')
    op.drop_index(op.f('ix_price_overrides_variant_id'), table_name='price_overrides')
    op.drop_index(op.f('ix_price_overrides_business_id'), table_name='price_overrides')
    op.drop_table('price_overrides')
    op.drop_index(op.f('ix_menu_item_aliases_business_id'), table_name='menu_item_aliases')
    op.drop_table('menu_item_aliases')
    op.drop_index('uq_variant_one_default', table_name='menu_item_variants')
    op.drop_index(op.f('ix_menu_item_variants_item_id'), table_name='menu_item_variants')
    op.drop_index(op.f('ix_menu_item_variants_business_id'), table_name='menu_item_variants')
    op.drop_table('menu_item_variants')
    op.drop_index(op.f('ix_menu_items_business_id'), table_name='menu_items')
    op.drop_table('menu_items')
    op.drop_index(op.f('ix_menu_categories_business_id'), table_name='menu_categories')
    op.drop_table('menu_categories')
