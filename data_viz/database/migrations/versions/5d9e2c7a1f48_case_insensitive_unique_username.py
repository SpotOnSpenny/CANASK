"""case-insensitive unique username

Revision ID: 5d9e2c7a1f48
Revises: 8e1f4a2b7c30
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5d9e2c7a1f48'
down_revision = '8e1f4a2b7c30'
branch_labels = None
depends_on = None


def upgrade():
    # Refuse with a readable list rather than a bare index-build failure if existing accounts
    # differ only by case -- those have to be renamed by hand before this can apply.
    clashes = op.get_bind().execute(sa.text(
        "SELECT lower(username), string_agg(username, ', ' ORDER BY id) FROM users "
        "GROUP BY lower(username) HAVING count(*) > 1"
    )).fetchall()
    if clashes:
        listing = "; ".join(names for _, names in clashes)
        raise RuntimeError(f"Usernames differing only by case must be renamed first: {listing}")

    op.create_index('uq_users_username_lower', 'users', [sa.text('lower(username)')], unique=True)


def downgrade():
    op.drop_index('uq_users_username_lower', table_name='users')
