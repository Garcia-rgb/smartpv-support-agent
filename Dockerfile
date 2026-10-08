FROM python:3.12-slim

LABEL org.opencontainers.image.title="SmartPV Support Agent" \
      org.opencontainers.image.description="Photovoltaic plant technical support agent: RAG, tool calling, human confirmation and offline evaluation" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/Garcia-rgb/smartpv-support-agent"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# static 必须排在 pip install 之前。pyproject 的 hatch force-include 会把它打进
# wheel（包内 fallback 路径），构建元数据阶段找不到 static 会让 pip install 直接失败
# （FileNotFoundError: Forced include not found: /app/static）。
COPY static ./static
RUN pip install --no-cache-dir .

# 内置客户端页面：服务按「环境变量 → 仓库布局 → 当前工作目录 → 包内」的顺序查找，
# 容器里的工作目录是 /app，所以上一步 COPY 进来的 static 就能被 GET / 命中。

# 不以 root 运行：容器里的进程不需要任何特权，而 uvicorn 监听的 8000 > 1024，
# 降权也不需要额外的 capabilities。SQLite 形态要往工作目录写库文件，
# 所以把 /app 的所有权交出去——只降权不交权，默认的 sqlite:///./support_agent.db 会建不出来。
# HOME 一起改掉：不给的话它仍是 /root，个别库会去写一个不可写的目录。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/operations /app/knowledge_base \
    && chown -R appuser:appuser /app
ENV HOME=/home/appuser
USER appuser

# 知识库语料不进镜像：容器起来后用 POST /documents 或挂载目录导入。
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "support_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
