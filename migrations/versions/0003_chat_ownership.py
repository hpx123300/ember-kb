"""给教练会话增加员工归属

Revision ID: 0003_chat_ownership
Revises: 0002_actions_summary
Create Date: 2026-10-19

历史上只靠客户端提供的 session_id 查询会话，知道 id 就能读取别人历史。
这一版把 employee 写进消息与摘要，接口再按当前登录用户校验归属。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003_chat_ownership"
down_revision = "0002_actions_summary"
branch_labels = None
depends_on = None


def _add_employee_column(table_name: str) -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "employee" in columns:
        return
    op.add_column(
        table_name,
        sa.Column("employee", sa.String(32), nullable=False, server_default=""),
    )
    op.create_index(
        f"ix_{table_name}_employee",
        table_name,
        ["employee"],
    )


def upgrade() -> None:
    _add_employee_column("chat_messages")
    _add_employee_column("session_summaries")


def downgrade() -> None:
    op.drop_index("ix_session_summaries_employee", table_name="session_summaries")
    op.drop_column("session_summaries", "employee")
    op.drop_index("ix_chat_messages_employee", table_name="chat_messages")
    op.drop_column("chat_messages", "employee")
