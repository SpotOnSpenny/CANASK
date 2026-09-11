"""password reset hardening: session_version + indexes

Revision ID: 8e1f4a2b7c30
Revises: 4c2bda4e9923
Create Date: 2026-09-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8e1f4a2b7c30'
down_revision = '4c2bda4e9923'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('session_version', sa.Integer(), nullable=False, server_default='1'))
    op.create_index('ix_password_resets_user_id_used_at', 'password_resets', ['user_id', 'used_at'])
    op.create_index('ix_invites_email_status', 'invites', ['email', 'status'])


def downgrade():
    op.drop_index('ix_invites_email_status', table_name='invites')
    op.drop_index('ix_password_resets_user_id_used_at', table_name='password_resets')
    op.drop_column('users', 'session_version')
