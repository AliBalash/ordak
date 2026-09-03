"""generation contract columns

Adds the explicit per-job generation contract (master_prompt §5, §18-21) and the
declared reference roles (§12-16, §61) so model/aspect/duration/resolution and each
upload's role reach the browser worker instead of being hardcoded.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0004"
down_revision = "20260715_0003"
branch_labels = None
depends_on = None


NEW_COLUMNS = ("references_json", "generation_json", "generation_receipt_json")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("jobs")]
    for name in NEW_COLUMNS:
        if name not in cols:
            with op.batch_alter_table("jobs") as batch_op:
                batch_op.add_column(sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("jobs")]
    for name in NEW_COLUMNS:
        if name in cols:
            with op.batch_alter_table("jobs") as batch_op:
                batch_op.drop_column(name)
