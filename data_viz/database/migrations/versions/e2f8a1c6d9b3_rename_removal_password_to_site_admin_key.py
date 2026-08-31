"""rename removal_password table to site_admin_key

The shared secret now gates every site-admin membership change (elevation, removal,
site-admin invites and their renewal), so "removal password" undersold it. Rename
only -- the stored hash and columns are untouched, so the current secret keeps working.

Revision ID: e2f8a1c6d9b3
Revises: c9d1e4f7a2b5
Create Date: 2026-08-31 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e2f8a1c6d9b3'
down_revision = 'c9d1e4f7a2b5'
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table('removal_password', 'site_admin_key')


def downgrade():
    op.rename_table('site_admin_key', 'removal_password')
