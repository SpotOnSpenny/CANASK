"""add data_sources.restricted (data sharing agreement flag)

A restricted source is closed to site admins who are not members of a group that holds it
(see data_viz/auth/restricted.py). Every existing row starts unrestricted; the flag is turned
on by `flask define-visuals` for sources whose manifest declares `"restricted": true`.

Revision ID: f3a8b6d1c2e9
Revises: d7f2a9c4e1b8
Create Date: 2026-11-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a8b6d1c2e9'
down_revision = 'd7f2a9c4e1b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('data_sources',
                  sa.Column('restricted', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    op.drop_column('data_sources', 'restricted')
