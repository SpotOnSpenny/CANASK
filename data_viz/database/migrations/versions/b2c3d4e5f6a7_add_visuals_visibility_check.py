"""add CHECK constraint on visuals.visibility

Constrains Visuals.visibility to the closed VISUAL_VISIBILITY set (private/group/public) at
the DB level. The UI write path already validates, but _can_see fails closed on any unknown
value, so a stray write would silently hide a visual -- this blocks that at the source. Any
pre-existing out-of-set rows are clamped to 'private' (most restrictive) before the constraint
is added so the migration can't fail on legacy data.

Revision ID: b2c3d4e5f6a7
Revises: 3650cbbadd03
Create Date: 2026-06-18 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = '3650cbbadd03'
branch_labels = None
depends_on = None

_CHECK = "visibility IN ('private', 'group', 'public')"


def upgrade():
    # Clamp any legacy/out-of-set value to the most restrictive level so the constraint can apply.
    op.execute(f"UPDATE visuals SET visibility = 'private' WHERE NOT ({_CHECK})")
    with op.batch_alter_table('visuals', schema=None) as batch_op:
        batch_op.create_check_constraint('ck_visuals_visibility', _CHECK)


def downgrade():
    with op.batch_alter_table('visuals', schema=None) as batch_op:
        batch_op.drop_constraint('ck_visuals_visibility', type_='check')
