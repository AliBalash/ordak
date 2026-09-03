"""flow video output"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0003"
down_revision = "20260714_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("jobs")]
    if "output_videos_json" not in cols:
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.add_column(sa.Column("output_videos_json", sa.Text(), nullable=True))
    if "metadata_json" not in cols:
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.add_column(sa.Column("metadata_json", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("jobs")]
    if "output_videos_json" in cols:
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_column("output_videos_json")
