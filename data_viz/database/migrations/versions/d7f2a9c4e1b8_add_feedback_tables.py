"""add feedback_submissions + feedback_notes tables

Revision ID: d7f2a9c4e1b8
Revises: 5d9e2c7a1f48
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7f2a9c4e1b8'
down_revision = '5d9e2c7a1f48'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('feedback_submissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('body', sa.String(length=5000), nullable=False),
    sa.Column('page', sa.String(length=512), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('ip_address', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('email_sent', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('email_error', sa.String(length=255), nullable=True),
    sa.Column('addressed_at', sa.DateTime(), nullable=True),
    sa.Column('addressed_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['addressed_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_feedback_submissions_created_at', 'feedback_submissions', ['created_at'], unique=False)
    op.create_table('feedback_notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('feedback_id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(length=2000), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['feedback_id'], ['feedback_submissions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_feedback_notes_feedback_id', 'feedback_notes', ['feedback_id'], unique=False)


def downgrade():
    op.drop_index('ix_feedback_notes_feedback_id', table_name='feedback_notes')
    op.drop_table('feedback_notes')
    op.drop_index('ix_feedback_submissions_created_at', table_name='feedback_submissions')
    op.drop_table('feedback_submissions')
