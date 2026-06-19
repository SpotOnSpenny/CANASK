"""drop dead visuals.substance column

The substance column recorded how the substance dimension should be filled when
encoding a visual's facts, but the cleaners now set substance dimensions
explicitly and nothing reads the column (its old gen-visuals/VISUAL_SPECS decoder
was removed). The manifests no longer author a "substance" key and
visual_definitions no longer writes it, so drop the column.

Revision ID: 2f582fc074ae
Revises: e74be72c260b
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f582fc074ae'
down_revision = 'e74be72c260b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('visuals', schema=None) as batch_op:
        batch_op.drop_column('substance')


def downgrade():
    # Re-add as it was created in 50cae918586a (nullable, never backfilled).
    with op.batch_alter_table('visuals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('substance', sa.String(length=50), nullable=True))
