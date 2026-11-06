"""Add LLM usage and cost columns to agent_runs.

Revision ID: 20261106_agent_run_usage
Revises: 20260909_token_disp_name
Create Date: 2026-11-06

Per-run token usage and USD cost reported by the Claude Agent SDK
ResultMessage (usage, total_cost_usd, model_usage). Stored as columns, not in
extra_metadata, so a daily budget can be summed in SQL. All nullable: runs
created before this revision, and runs that never reached a ResultMessage,
have no usage.

alembic_version.version_num is varchar(32); keep the revision id short.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261106_agent_run_usage"
down_revision = "20260909_token_disp_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("agent_runs", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("output_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "agent_runs",
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "model_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "model_usage")
    op.drop_column("agent_runs", "cache_creation_input_tokens")
    op.drop_column("agent_runs", "cache_read_input_tokens")
    op.drop_column("agent_runs", "output_tokens")
    op.drop_column("agent_runs", "input_tokens")
    op.drop_column("agent_runs", "cost_usd")
