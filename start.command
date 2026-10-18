#!/bin/bash
# 薪火 Ember 一键启动
# 双击本文件：自动安装后端依赖 → 构建前端 → 启动服务 → 等服务就绪后自动打开浏览器

cd "$(dirname "$0")" || exit 1

# 1. 后端虚拟环境与依赖
if [ ! -d ".venv" ]; then
  echo "首次运行：创建虚拟环境并安装后端依赖（可能需要 1-2 分钟）..."
  python3 -m venv .venv || exit 1
  .venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    || .venv/bin/pip install -r requirements.txt \
    || exit 1
fi

# 2. 配置文件检查（没有 .env 就自动生成）
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "已生成 .env 文件。"
fi
if ! grep -q "^DEEPSEEK_API_KEY=.\+" .env 2>/dev/null; then
  echo "提示：.env 里还没有填写 DEEPSEEK_API_KEY，界面可以打开，"
  echo "但 AI 功能需要填写 Key 后重新运行本文件才能使用。"
fi

# 3. 构建前端（首次或 dist 缺失时）
if [ ! -f "web/dist/index.html" ]; then
  echo "首次运行：安装前端依赖并构建（需要 Node.js，约 1-2 分钟）..."
  cd web || exit 1
  if command -v npm >/dev/null 2>&1; then
    npm install && npm run build || exit 1
  elif command -v pnpm >/dev/null 2>&1; then
    pnpm install && pnpm build || exit 1
  else
    echo "未找到 npm/pnpm，请先安装 Node.js 后重试。"
    exit 1
  fi
  cd ..
fi

# 4. 端口占用检查：8001 被本项目占用时直接打开浏览器；被其他程序占用则提示冲突
if lsof -nP -iTCP:8001 -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -fsS http://127.0.0.1:8001/api/health >/dev/null 2>&1; then
    echo "服务已在运行，正在打开浏览器：http://localhost:8001"
    open http://localhost:8001
    exit 0
  else
    echo "错误：端口 8001 被其他程序占用（不是本项目的服务，请勿直接打开）。"
    echo "请先关闭占用 8001 的程序（查看：lsof -nP -iTCP:8001），再重新双击本文件。"
    exit 1
  fi
fi

# 5. 后台启动 FastAPI 服务
echo "正在启动 AI 面试智能系统（新版界面），请稍候…"
.venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8001 &
SERVER_PID=$!

# 6. 等服务就绪后再打开浏览器（最多 60 秒）
READY=0
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8001/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" = "1" ]; then
  echo "启动完成，正在打开浏览器：http://localhost:8001"
  open http://localhost:8001
else
  echo "服务启动超时，请看上方错误信息；也可手动打开 http://localhost:8001"
fi

# 7. 保持前台运行，关闭本窗口即停止服务
wait $SERVER_PID
