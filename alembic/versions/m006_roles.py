"""phase 1 roles: staff roles, system actor, sessions, login attempts, staff channels

Revision ID: m006_roles
Revises: m5_0001
Create Date: 2026-11-26 01:00:00
"""
import uuid

import sqlalchemy as sa

from alembic import op

revision = 'm006_roles'
down_revision = 'm5_0001'
branch_labels = None
depends_on = None

ROLES = ('owner', 'manager', 'cashier', 'kitchen', 'storekeeper', 'delivery', 'system')
STAFF_ROLES = ROLES[:-1]


def _system_user_id(business_id: uuid.UUID) -> uuid.UUID:
    # Same derivation as app/models_roles.system_user_id (uuid5(business_id, "system")).
    return uuid.uuid5(business_id, "system")


def upgrade() -> None:
    op.add_column('users', sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(), server_default='false', nullable=False))
    op.create_unique_constraint('uq_users_business_id', 'users', ['business_id', 'id'])
    op.create_check_constraint('ck_users_role', 'users', "role in (" + ",".join(f"'{r}'" for r in ROLES) + ")")

    # One internal system actor per business (proposal v3, C2). It can never log in:
    # is_active=false and password_hash NULL. Deterministic id, so code finds it without a lookup.
    bind = op.get_bind()
    users = sa.table(
        'users',
        sa.column('id', sa.UUID), sa.column('business_id', sa.UUID),
        sa.column('full_name', sa.String), sa.column('email', sa.String),
        sa.column('role', sa.String), sa.column('password_hash', sa.String),
        sa.column('is_active', sa.Boolean), sa.column('must_change_password', sa.Boolean),
    )
    for (biz_id,) in bind.execute(sa.text('select id from businesses')):
        bind.execute(users.insert().values(
            id=_system_user_id(biz_id), business_id=biz_id, full_name='System',
            email=f'system+{biz_id}@internal.waseet', role='system',
            password_hash=None, is_active=False, must_change_password=False,
        ))

    op.create_table(
        'user_sessions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('family_id', sa.UUID(), nullable=False),
        sa.Column('refresh_token_hash', sa.String(length=64), nullable=False),
        sa.Column('issued_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(['business_id', 'user_id'], ['users.business_id', 'users.id'],
                                name='fk_session_user_same_business', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('refresh_token_hash'),
    )
    op.create_index(op.f('ix_user_sessions_business_id'), 'user_sessions', ['business_id'])
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'])
    op.create_index(op.f('ix_user_sessions_family_id'), 'user_sessions', ['family_id'])

    op.create_table(
        'login_attempts',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('email_norm', sa.String(length=320), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_login_attempts_email_at', 'login_attempts', ['email_norm', 'at'])
    op.create_index('ix_login_attempts_ip_at', 'login_attempts', ['ip', 'at'])

    op.create_table(
        'staff_channels',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=True),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('address', sa.String(length=64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role in (" + ",".join(f"'{r}'" for r in STAFF_ROLES) + ")",
                           name='ck_staff_channels_role'),
        sa.CheckConstraint("channel in ('telegram')", name='ck_staff_channels_channel'),
        sa.CheckConstraint("(role is null) <> (user_id is null)", name='ck_staff_channels_one_target'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_id', 'user_id'], ['users.business_id', 'users.id'],
                                name='fk_staffchannel_user_same_business', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_id', 'role', 'channel', name='uq_staff_channels_role'),
        sa.UniqueConstraint('business_id', 'user_id', 'channel', name='uq_staff_channels_user'),
    )
    op.create_index(op.f('ix_staff_channels_business_id'), 'staff_channels', ['business_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_staff_channels_business_id'), table_name='staff_channels')
    op.drop_table('staff_channels')
    op.drop_index('ix_login_attempts_ip_at', table_name='login_attempts')
    op.drop_index('ix_login_attempts_email_at', table_name='login_attempts')
    op.drop_table('login_attempts')
    op.drop_index(op.f('ix_user_sessions_family_id'), table_name='user_sessions')
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_index(op.f('ix_user_sessions_business_id'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.execute("delete from users where role = 'system'")
    op.drop_constraint('ck_users_role', 'users', type_='check')
    op.drop_constraint('uq_users_business_id', 'users', type_='unique')
    op.drop_column('users', 'must_change_password')
    op.drop_column('users', 'is_active')
