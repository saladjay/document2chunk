# mineru2doc HTTP API（CPU-only，调宿主 MinerU :9030）
FROM python:3.12-slim

WORKDIR /app

# 只需 mineru2doc 包（独立，不依赖 src/document2chunk）
COPY mineru2doc ./mineru2doc

RUN pip install --no-cache-dir httpx==0.28.* fastapi uvicorn python-multipart

ENV MINERU_BASE_URL=http://host.docker.internal:9030 \
    MINERU_TIMEOUT=300 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "mineru2doc.server:app", "--host", "0.0.0.0", "--port", "8000"]
