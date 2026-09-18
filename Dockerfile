FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Statische Dateien einsammeln; der Schluessel wird dafuer nicht gebraucht.
RUN DJANGO_DEBUG=true python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
CMD ["gunicorn", "zeiterfassung.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
