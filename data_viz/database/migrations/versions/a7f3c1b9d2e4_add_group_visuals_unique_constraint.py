"""add unique constraint on group_visuals(group_id, visual_id)

GroupVisuals lacked the (group_id, visual_id) uniqueness its sibling
GroupDataSources has. set_group_visuals dedupes grants in Python today, but any
other writer (seed/manual) could insert duplicate grants that allowed_visuals
would then double-count. Add the constraint, deduping any pre-existing rows
first (keep the lowest id) so the constraint can be applied to existing data.

Revision ID: a7f3c1b9d2e4
Revises: 2f582fc074ae
Create Date: 2026-06-23 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a7f3c1b9d2e4'
down_revision = '2f582fc074ae'
branch_labels = None
depends_on = None


def upgrade():
    # Drop any duplicate (group_id, visual_id) grants, keeping the lowest id, so the new constraint
    # can be created against existing data.
    op.execute(
        "DELETE FROM group_visuals a USING group_visuals b "
        "WHERE a.id > b.id AND a.group_id = b.group_id AND a.visual_id = b.visual_id"
    )
    with op.batch_alter_table('group_visuals', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_group_visual', ['group_id', 'visual_id'])


def downgrade():
    with op.batch_alter_table('group_visuals', schema=None) as batch_op:
        batch_op.drop_constraint('uq_group_visual', type_='unique')
