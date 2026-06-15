"""Create export request storage for GDPR personal data exports."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260429_gdpr_export_requests"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create tables required for the export flow."""

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("locale", sa.String(length=32), nullable=False),
        sa.Column("phone_number", sa.String(length=64), nullable=True),
        sa.Column("address_line1", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_table(
        "export_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_export_requests_user_id", "export_requests", ["user_id"])


def downgrade() -> None:
    """Drop tables required for the export flow."""

    op.drop_index("ix_export_requests_user_id", table_name="export_requests")
    op.drop_table("export_requests")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("user_profiles")
    op.drop_table("users")
