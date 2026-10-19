"""新增 pending_actions 与 session_summaries

Revision ID: 0002_actions_summary
Revises: 0001_baseline
Create Date: 2026-10-19

两张表对应两个新能力：
- pending_actions：Agent 的写操作先落在这里，等人确认后才执行；
- session_summaries：会话的滚动摘要，替代「超过 N 条就丢掉」的硬截断。

都用「表不存在才建」的写法，和 0001 保持一致：新库与老库都能跑到同一个终态，
重复执行不会报错。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_actions_summary"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())

    if "pending_actions" not in existing:
        op.create_table(
            "pending_actions",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("session_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("employee", sa.String(32), nullable=False, server_default="local"),
            sa.Column("kind", sa.String(32), nullable=False, server_default="append_faq"),
            sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("preview", sa.Text(), nullable=False, server_default=""),
            sa.Column("result", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("decided_by", sa.String(32), nullable=False, server_default=""),
        )
        op.create_index("ix_pending_actions_session_id", "pending_actions", ["session_id"])
        op.create_index("ix_pending_actions_employee", "pending_actions", ["employee"])
        op.create_index("ix_pending_actions_status", "pending_actions", ["status"])
        op.create_index("ix_pending_actions_created_at", "pending_actions", ["created_at"])

    if "session_summaries" not in existing:
        op.create_table(
            "session_summaries",
            sa.Column("session_id", sa.String(64), primary_key=True),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("covered_until_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("session_summaries")
    op.drop_table("pending_actions")
