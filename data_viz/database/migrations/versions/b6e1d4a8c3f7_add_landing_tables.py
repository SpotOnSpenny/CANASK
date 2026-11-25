"""add group_landing_visuals + user_landing_choices + user_starting_views (starting charts)

A province page used to open on one visual for everybody (visuals.is_default), which any Data
Owner could move. Each group now picks the visual its own members open a province on
(group_landing_visuals), and any user can set their own starting chart for a page, from the
charts they can see (user_landing_choices). visuals.is_default stays as the site-wide default.
user_starting_views holds the same kind of personal setting for the two pages that have no chart
list (Confluence, the DAS Explorer): the page's settings as a query string.
No backfill: no group has a pick until one of its Data Owners sets it.

Revision ID: b6e1d4a8c3f7
Revises: f3a8b6d1c2e9
Create Date: 2026-11-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b6e1d4a8c3f7'
down_revision = 'f3a8b6d1c2e9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'group_landing_visuals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('province', sa.String(length=255), nullable=False),
        sa.Column('visual_id', sa.Integer(), nullable=False),
        sa.Column('set_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['visual_id'], ['visuals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['set_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'province', name='uq_group_landing_province'),
    )
    op.create_table(
        'user_landing_choices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('province', sa.String(length=255), nullable=False),
        sa.Column('visual_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['visual_id'], ['visuals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'province', name='uq_user_landing_province'),
    )


    op.create_table(
        'user_starting_views',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('page', sa.String(length=50), nullable=False),
        sa.Column('query_string', sa.String(length=2000), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'page', name='uq_user_starting_view_page'),
    )


def downgrade():
    op.drop_table('user_starting_views')
    op.drop_table('user_landing_choices')
    op.drop_table('group_landing_visuals')
