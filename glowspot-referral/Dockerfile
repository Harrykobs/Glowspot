FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV APP_ENV=production TRUST_PROXY=1
EXPOSE 8000
CMD ["gunicorn", "wsgi:app", "--workers", "2", "--timeout", "60", "--bind", "0.0.0.0:8000"]
