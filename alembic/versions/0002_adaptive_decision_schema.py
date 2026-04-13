from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_adaptive_decision_schema"
down_revision = "0001_sqlite_first_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "recipes" in table_names:
        recipe_columns = {column["name"] for column in inspector.get_columns("recipes")}
        if "prep_minutes" not in recipe_columns:
            op.add_column("recipes", sa.Column("prep_minutes", sa.Integer(), nullable=False, server_default="10"))
        if "step_count" not in recipe_columns:
            op.add_column("recipes", sa.Column("step_count", sa.Integer(), nullable=False, server_default="3"))
        if "effort_score" not in recipe_columns:
            op.add_column("recipes", sa.Column("effort_score", sa.Float(), nullable=False, server_default="0.4"))
        if "suitable_when_tired" not in recipe_columns:
            op.add_column("recipes", sa.Column("suitable_when_tired", sa.Boolean(), nullable=False, server_default=sa.true()))

    if "user_preferences" not in table_names:
        op.create_table(
            "user_preferences",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="lazy"),
            sa.Column("meal_window_start", sa.String(length=10), nullable=False, server_default="18:00"),
            sa.Column("meal_window_end", sa.String(length=10), nullable=False, server_default="21:00"),
            sa.Column("late_night_window_start", sa.String(length=10), nullable=False, server_default="22:30"),
            sa.Column("late_night_window_end", sa.String(length=10), nullable=False, server_default="00:30"),
            sa.Column("max_prep_minutes", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("notification_frequency", sa.String(length=20), nullable=False, server_default="normal"),
            sa.Column("dietary_preferences", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_user_preferences_user_id"),
        )

    if "temporary_state_overrides" not in table_names:
        op.create_table(
            "temporary_state_overrides",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("state", sa.String(length=50), nullable=False),
            sa.Column("value", sa.String(length=255), nullable=False, server_default="active"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="telegram"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "decision_profiles" not in table_names:
        op.create_table(
            "decision_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("ignore_nudge_rate", sa.Float(), nullable=False, server_default="0"),
            sa.Column("healthy_meal_acceptance_score", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("quick_food_bias", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("eat_at_home_likelihood", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("stress_eating_signal", sa.Float(), nullable=False, server_default="0"),
            sa.Column("user_threshold", sa.Float(), nullable=False, server_default="0.6"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_decision_profiles_user_id"),
        )

    if "assistant_interventions" not in table_names:
        op.create_table(
            "assistant_interventions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("thread_key", sa.String(length=255), nullable=False),
            sa.Column("sequence_index", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("context_hash", sa.String(length=255), nullable=False),
            sa.Column("decision_type", sa.String(length=100), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="sent"),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("mute_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("recommended_action", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("reason_codes", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("draft_items", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "assistant_interventions" in table_names:
        op.drop_table("assistant_interventions")
    if "decision_profiles" in table_names:
        op.drop_table("decision_profiles")
    if "temporary_state_overrides" in table_names:
        op.drop_table("temporary_state_overrides")
    if "user_preferences" in table_names:
        op.drop_table("user_preferences")
