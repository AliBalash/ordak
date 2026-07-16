"""ordex agent mode"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_0002"
down_revision = "20260704_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("agent_workspace", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("agent_max_steps", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("agent_command_timeout_seconds", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("agent_execution_backend", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("agent_network_enabled", sa.Boolean(), nullable=True))

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("job_id", "sequence", name="uq_agent_steps_job_sequence"),
        sa.UniqueConstraint("job_id", "command_id", name="uq_agent_steps_job_command_id"),
    )
    op.create_index("ix_agent_steps_job_id", "agent_steps", ["job_id"])
    op.create_index("ix_agent_steps_status", "agent_steps", ["status"])


def downgrade() -> None:
    op.drop_index("ix_agent_steps_status", table_name="agent_steps")
    op.drop_index("ix_agent_steps_job_id", table_name="agent_steps")
    op.drop_table("agent_steps")

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("agent_network_enabled")
        batch_op.drop_column("agent_execution_backend")
        batch_op.drop_column("agent_command_timeout_seconds")
        batch_op.drop_column("agent_max_steps")
        batch_op.drop_column("agent_workspace")
