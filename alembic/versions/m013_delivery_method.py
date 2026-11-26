"""m013: customers.delivery_method for mandatory onboarding (spec 19758722 F2).

delivery vs pickup is CRM data: returning customers must not be re-asked, so it
persists on the customer (phone/address columns already exist). Nullable:
existing customers fill it on their next visit through the onboarding gate.
"""

from alembic import op
import sqlalchemy as sa

revision = "m013_delivery_method"
down_revision = "m012_autolink_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("delivery_method", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_customers_delivery_method", "customers",
        "delivery_method in ('delivery','pickup')")


def downgrade() -> None:
    op.drop_constraint("ck_customers_delivery_method", "customers", type_="check")
    op.drop_column("customers", "delivery_method")
