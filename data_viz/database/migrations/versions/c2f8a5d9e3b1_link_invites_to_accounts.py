"""link accepted invites to the accounts they created

Users can now change their own username and email, so an accepted invite can no longer be matched
to its account by email. Each accepted invite records the account it created
(accepted_user_id), the username that person signed up with (accepted_username) and when
(accepted_at); invites.email stays the address the invite was sent to.

Backfill: until this release nobody could change their email or username, so every already
accepted invite still matches its account by email and that account still has its original
username. accepted_at comes from the "account created via invite" activity row where there is one.

Revision ID: c2f8a5d9e3b1
Revises: b6e1d4a8c3f7
Create Date: 2026-11-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2f8a5d9e3b1'
down_revision = 'b6e1d4a8c3f7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('invites', sa.Column('accepted_user_id', sa.Integer(), nullable=True))
    op.add_column('invites', sa.Column('accepted_username', sa.String(length=255), nullable=True))
    op.add_column('invites', sa.Column('accepted_at', sa.DateTime(), nullable=True))
    op.create_foreign_key('fk_invites_accepted_user', 'invites', 'users',
                          ['accepted_user_id'], ['id'], ondelete='SET NULL')
    op.execute("""
        UPDATE invites
           SET accepted_user_id = users.id,
               accepted_username = users.username
          FROM users
         WHERE invites.status = 'accepted'
           AND lower(users.email) = lower(invites.email)
    """)
    op.execute("""
        UPDATE invites
           SET accepted_at = created.first_at
          FROM (SELECT user_id, min(timestamp) AS first_at
                  FROM user_activity
                 WHERE activity_type = 'account created via invite'
                 GROUP BY user_id) AS created
         WHERE invites.accepted_user_id = created.user_id
    """)


def downgrade():
    op.drop_constraint('fk_invites_accepted_user', 'invites', type_='foreignkey')
    op.drop_column('invites', 'accepted_at')
    op.drop_column('invites', 'accepted_username')
    op.drop_column('invites', 'accepted_user_id')
