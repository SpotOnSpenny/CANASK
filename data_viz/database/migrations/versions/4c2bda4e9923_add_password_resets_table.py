"""add password resets table

Revision ID: 4c2bda4e9923
Revises: e2f8a1c6d9b3
Create Date: 2026-09-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4c2bda4e9923'
down_revision = 'e2f8a1c6d9b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('password_resets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('requested_ip', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('password_resets')
