FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hashed + compressed static files, served by WhiteNoiseMiddleware out of
# STATIC_ROOT. This ENV has to outlive the build: it selects the backend for the
# collectstatic below *and* for the running container, so the two can't disagree
# about whether a manifest exists. Safe at build time — no DB and no secrets
# needed, since config/settings.py has decouple defaults for everything.
ENV STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
