"""milestone 5 chat sessions

Revision ID: m5_0001
Revises: m4_0001
Create Date: 2026-11-25 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'm5_0001'
down_revision = 'm4_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('chat_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('draft', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id', 'customer_id'], ['customers.business_id', 'customers.id'], name='fk_chatsession_customer_same_business', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('chat_id')
    )
    op.create_index(op.f('ix_chat_sessions_business_id'), 'chat_sessions', ['business_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_chat_sessions_business_id'), table_name='chat_sessions')
    op.drop_table('chat_sessions')
