"""ordak core upgrade"""

from alembic import op
import sqlalchemy as sa


revision = "20260704_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column("external_conversation_id", sa.Text(), nullable=True),
        sa.Column("tab_window_id", sa.Integer(), nullable=True),
        sa.Column("tab_id", sa.Integer(), nullable=True),
        sa.Column("tab_window_key", sa.Text(), nullable=True),
        sa.Column("tab_target_id", sa.Text(), nullable=True),
        sa.Column("tab_alive", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_successful_job_id", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("conversation_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("conversation_title", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("mode", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("start_new_chat", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("error_code", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("retry_of_job_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("run_strategy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("recoverable", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("suggested_action", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("uploads_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("output_images_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("output_images_json")
        batch_op.drop_column("uploads_json")
        batch_op.drop_column("cancel_requested_at")
        batch_op.drop_column("suggested_action")
        batch_op.drop_column("recoverable")
        batch_op.drop_column("run_strategy")
        batch_op.drop_column("retry_of_job_id")
        batch_op.drop_column("error_code")
        batch_op.drop_column("start_new_chat")
        batch_op.drop_column("mode")
        batch_op.drop_column("conversation_title")
        batch_op.drop_column("conversation_id")
        batch_op.drop_column("provider")
    op.drop_table("conversations")
