"""add per-visual visibility (private/group/public) to visuals

Adds the Visuals.visibility access level. Existing rows are defaulted to the most
restrictive level ('private') so nothing is exposed until a Data Owner opts in, EXCEPT
sourceless drill maps (data_source_id IS NULL), which carry no data and exist only to
host drillable children -- they are set to 'public' so navigation isn't bricked (their
children's own visibility still gates the real data).

Revision ID: a1b2c3d4e5f6
Revises: 53280e8f501e
Create Date: 2026-06-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '53280e8f501e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('visuals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('visibility', sa.String(length=20),
                                      nullable=False, server_default='private'))
    # Scaffolding maps carry no data of their own (visual_options only) and exist to host drillable
    # children -> keep them reachable for everyone so navigation isn't bricked. This matches the
    # default applied by generateVisuals.export_data_to_db for map_none / sourceless visuals.
    op.execute("UPDATE visuals SET visibility = 'public' "
               "WHERE data_source_id IS NULL OR data_shape = 'map_none'")


def downgrade():
    with op.batch_alter_table('visuals', schema=None) as batch_op:
        batch_op.drop_column('visibility')
