# Iwfm backend — Railway / bármely Docker host
FROM python:3.12-slim

WORKDIR /app

# DejaVu font a munkalap-PDF magyar ékezeteihez (ő, ű)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV WFM_ENVIRONMENT=production
# Railway a PORT env varban adja a portot. A --proxy-headers + forwarded-allow-ips
# kell, hogy a Railway reverse-proxy mögött a valós kliens-IP-t lássuk
# (login/kiosk rate-limit kulcsok és az audit-napló IP-je ebből számol).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
