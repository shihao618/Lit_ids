# syntax=docker/dockerfile:1
# LIT API Gateway —— 洛阳理工学院统一认证接口服务（多用户 CAS 网关）
# 规范要点：非 root 运行、固定依赖版本、最小镜像、HEALTHCHECK、OCI 标签、数据卷。
FROM python:3.12-slim-bookworm

ARG APP_VERSION=0.1.0

LABEL org.opencontainers.image.title="LIT API Gateway" \
      org.opencontainers.image.description="洛阳理工学院统一认证接口服务（多用户 CAS 网关）" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    DATA_DIR=/data

WORKDIR /app

# 时区（日志用北京时间）+ 非 root 运行用户
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r appuser \
 && useradd -r -g appuser -M appuser

# Python 依赖：固定版本保证可复现，单独一层利用构建缓存
# 国内网络慢可通过 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 加速
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt ./
RUN pip install --no-cache-dir --timeout 120 --retries 10 \
      --index-url "${PIP_INDEX_URL}" -r requirements.txt

# 源码：统一认证核心 + 接口框架（属主直接给运行用户，容器内不需要写 /app）
COPY --chown=appuser:appuser lit_login.py ./
COPY --chown=appuser:appuser app ./app

# 持久化数据目录（compose 中挂载 ./data:/data）
RUN mkdir -p /data && chown appuser:appuser /data
VOLUME ["/data"]

USER appuser

EXPOSE 10381
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:10381/healthz', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10381"]
