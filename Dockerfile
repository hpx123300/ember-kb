# 薪火 Ember · 单容器部署
# 前端在构建阶段打包，运行阶段只留 Python 与静态产物
FROM node:20-slim AS web
WORKDIR /app/web
COPY web/package*.json ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY server/ ./server/
COPY scripts/ ./scripts/
COPY data/knowledge/ ./data/knowledge/
COPY data/eval/ ./data/eval/
COPY --from=web /app/web/dist ./web/dist

EXPOSE 8001
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8001"]
