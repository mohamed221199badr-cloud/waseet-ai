"""milestone 4 telegram intake

Revision ID: m4_0001
Revises: m3_0001
Create Date: 2026-11-25 10:30:00
"""
from alembic import op
import sqlalchemy as sa

revision = 'm4_0001'
down_revision = 'm3_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('telegram_link_codes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id', 'customer_id'], ['customers.business_id', 'customers.id'], name='fk_tglinkcode_customer_same_business', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    op.create_index(op.f('ix_telegram_link_codes_business_id'), 'telegram_link_codes', ['business_id'], unique=False)
    op.create_table('telegram_links',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id', 'customer_id'], ['customers.business_id', 'customers.id'], name='fk_tglink_customer_same_business', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('chat_id')
    )
    op.create_index(op.f('ix_telegram_links_business_id'), 'telegram_links', ['business_id'], unique=False)
    op.create_table('telegram_updates',
    sa.Column('update_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('update_id')
    )


def downgrade() -> None:
    op.drop_table('telegram_updates')
    op.drop_index(op.f('ix_telegram_links_business_id'), table_name='telegram_links')
    op.drop_table('telegram_links')
    op.drop_index(op.f('ix_telegram_link_codes_business_id'), table_name='telegram_link_codes')
    op.drop_table('telegram_link_codes')
