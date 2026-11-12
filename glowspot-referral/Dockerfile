FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV APP_ENV=production DATABASE_PATH=/data/glowspot.db TRUST_PROXY=1
VOLUME /data
EXPOSE 8000
CMD ["gunicorn", "wsgi:app", "--workers", "2", "--timeout", "60", "--bind", "0.0.0.0:8000"]
