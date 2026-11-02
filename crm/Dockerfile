# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY crm_service ./crm_service
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home crm
COPY alembic.ini ./
COPY alembic ./alembic
RUN chmod -R a+rX /app
USER crm
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn crm_service.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
