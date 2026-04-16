from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_user_preference_food_lists"
down_revision = "0003_user_preference_search_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "user_preferences" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("user_preferences")}
    if "essentials_items" not in columns:
        op.add_column(
            "user_preferences",
            sa.Column(
                "essentials_items",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
        op.execute(
            "UPDATE user_preferences SET essentials_items = '[\"milk\",\"eggs\",\"rice\",\"pasta\"]' WHERE essentials_items = '[]'"
        )
    if "dairy_items" not in columns:
        op.add_column(
            "user_preferences",
            sa.Column(
                "dairy_items",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
        op.execute(
            "UPDATE user_preferences SET dairy_items = '[\"milk\",\"yogurt\",\"cheese\",\"butter\",\"cream\"]' WHERE dairy_items = '[]'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "user_preferences" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("user_preferences")}
    if "dairy_items" in columns:
        op.drop_column("user_preferences", "dairy_items")
    if "essentials_items" in columns:
        op.drop_column("user_preferences", "essentials_items")
