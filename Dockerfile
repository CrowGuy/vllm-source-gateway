FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && \
    pip install .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG APP_UID=10001
ARG APP_GID=10001

WORKDIR /app

COPY --from=builder /usr/local /usr/local

RUN groupadd --gid "${APP_GID}" app && \
    useradd --uid "${APP_UID}" --gid app --create-home --shell /usr/sbin/nologin app

USER app:app

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "vllm_source_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
