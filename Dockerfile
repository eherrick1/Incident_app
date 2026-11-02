# Mirrors the expense-approval app's container: python:3.12-slim + gunicorn
# + LibreOffice headless (calc) for Excel -> PDF.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-calc \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 300 app:app
