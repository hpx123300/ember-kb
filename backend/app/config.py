"""全局配置：从 .env 读取，所有模块从这里拿配置。"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# 大模型
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Agent 单轮最大工具调用步数（防止死循环，可按需调大）
AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "6"))

# 检索参数
RAG_CHUNK_MAX_CHARS = int(os.getenv("RAG_CHUNK_MAX_CHARS", "500"))
RAG_CHUNK_OVERLAP_CHARS = int(os.getenv("RAG_CHUNK_OVERLAP_CHARS", "80"))
RAG_RRF_K = int(os.getenv("RAG_RRF_K", "60"))

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
# 测试时指向临时库，避免把 mock 产生的调用记录写进开发库
DB_PATH = Path(os.getenv("EMBER_DB_PATH", str(DATA_DIR / "ember.db")))
RAG_INDEX_DIR = DATA_DIR / "rag_index"

# 数据库连接：默认 SQLite 文件；生产直接给 DATABASE_URL（如 mysql+pymysql://...）
# 代码层只认这一个变量，切库不用改任何业务代码。
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# 鉴权：签发 HS256 JWT。生产必须设置 EMBER_JWT_SECRET，否则重启即失效。
JWT_SECRET = os.getenv("EMBER_JWT_SECRET", "ember-dev-secret-change-me-in-production-0123456789")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_HOURS = int(os.getenv("EMBER_TOKEN_TTL_HOURS", "12"))

# 演示账号（首次启动时自动创建，密码只在没有同名用户时写入）
DEFAULT_ADMIN = os.getenv("EMBER_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("EMBER_ADMIN_PASSWORD", "admin12345")


def ensure_dirs() -> None:
    for d in (DATA_DIR, KNOWLEDGE_DIR, RAG_INDEX_DIR):
        d.mkdir(parents=True, exist_ok=True)
