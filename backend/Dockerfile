# Iwfm backend — Railway / bármely Docker host
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV WFM_ENVIRONMENT=production
# Railway a PORT env varban adja a portot.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
