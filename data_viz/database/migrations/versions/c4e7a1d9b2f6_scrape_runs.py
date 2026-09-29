"""scrape runs, source settings, report subscription

Revision ID: c4e7a1d9b2f6
Revises: 8e1f4a2b7c30
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e7a1d9b2f6'
down_revision = '8e1f4a2b7c30'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("data_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("scraped_on", sa.Date(), nullable=True),
        sa.Column("data_until", sa.Date(), nullable=True),
        sa.Column("previous_data_until", sa.Date(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("s3_key", sa.String(512), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("check_results", sa.JSON(), nullable=True),
        sa.Column("notices", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("rollback_of_run_id", sa.Integer(), sa.ForeignKey("scrape_runs.id"), nullable=True),
    )
    op.create_index("ix_scrape_runs_source_key", "scrape_runs", ["source_key"])
    op.create_index("ix_scrape_runs_status", "scrape_runs", ["status"])
    op.create_index("uq_scrape_runs_active_source", "scrape_runs", ["source_key"], unique=True,
                    postgresql_where=sa.text("is_active"))
    op.create_table(
        "source_settings",
        sa.Column("source_key", sa.String(64), primary_key=True),
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_publish", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paused_reason", sa.String(255), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.add_column("users", sa.Column("data_report_subscribed", sa.Boolean(), nullable=False,
                                     server_default=sa.false()))


def downgrade():
    op.drop_column("users", "data_report_subscribed")
    op.drop_table("source_settings")
    op.drop_index("uq_scrape_runs_active_source", table_name="scrape_runs")
    op.drop_index("ix_scrape_runs_status", table_name="scrape_runs")
    op.drop_index("ix_scrape_runs_source_key", table_name="scrape_runs")
    op.drop_table("scrape_runs")
