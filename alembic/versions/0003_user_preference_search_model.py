from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.core.search_models import DEFAULT_SEARCH_MODEL

revision = "0003_user_preference_search_model"
down_revision = "0002_adaptive_decision_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "user_preferences" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("user_preferences")}
    if "search_model" not in columns:
        op.add_column(
            "user_preferences",
            sa.Column(
                "search_model",
                sa.String(length=50),
                nullable=False,
                server_default=DEFAULT_SEARCH_MODEL,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "user_preferences" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("user_preferences")}
    if "search_model" in columns:
        op.drop_column("user_preferences", "search_model")

