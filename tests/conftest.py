"""测试公共配置：保证从项目根导入，并初始化数据库。

两个隔离点：

1. 数据库指向临时文件，避免测试写脏开发库（指标表尤其明显——
   一次测试会写几百行 0 token 的假调用，把 /api/metrics 的调用数撑大）。
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 必须在 import backend.app.* 之前设置：config.py 在导入时就会读这些变量。
# 这里用强制赋值而不是 setdefault——测试必须和开发库彻底隔离，
# 不能因为 shell 里恰好设过同名变量就写回真实库。
_TMP_DIR = Path(tempfile.mkdtemp(prefix="interview-test-"))
os.environ["EMBER_DB_PATH"] = str(_TMP_DIR / "interview.db")

# CI / 无 .env 环境下也能跑：配置一个占位 Key（所有 LLM 调用均被 mock）
os.environ.setdefault("DEEPSEEK_API_KEY", "test-dummy-key")

from backend.app.db import init_db  # noqa: E402

init_db()
