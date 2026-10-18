"""baseline：把 create_all 建出来的库收敛到迁移管理之下

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-18

这是一支「收敛型」迁移，不是 autogenerate 出来的。

背景：项目早期直接 Base.metadata.create_all() 建表，线上/本地都已经有数据。
如果按标准做法从「空库」开始生成，已有库会因为表已存在而直接失败。

所以这一支做三件事，都可以重复执行：
1. 补建缺失的表；
2. 给已有表补上新增的列（例如 llm_calls.trace_id）；
3. 建立 alembic_version，从此版本号唯一。

新库跑这一支得到完整结构，老库跑这一支被补齐——两种起点收敛到同一个终态。
之后的每次改表都应该是正常的 autogenerate 迁移。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _add_missing_columns(conn) -> None:
    from backend.app.db import Base

    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all 已经建好，不用补列
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            ddl_type = column.type.compile(dialect=conn.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'
            default = getattr(column, "default", None)
            if default is not None and getattr(default, "is_scalar", False):
                value = default.arg
                if isinstance(value, bool):
                    ddl += f" DEFAULT {1 if value else 0}"
                elif isinstance(value, (int, float)):
                    ddl += f" DEFAULT {value}"
                elif isinstance(value, str):
                    escaped = value.replace("'", "''")
                    ddl += f" DEFAULT '{escaped}'"
            conn.execute(sa.text(ddl))


def upgrade() -> None:
    conn = op.get_bind()
    # create_all 只会建缺失的表，已有的表原样保留
    from backend.app.db import Base

    Base.metadata.create_all(bind=conn)
    _add_missing_columns(conn)


def downgrade() -> None:
    # 基线版本不提供回滚：删表等于删数据，交给人手动决定
    pass
