# A named stage, so Dependabot bumps uv too. Keep it at or above `required-version` in pyproject.toml.
FROM ghcr.io/astral-sh/uv:0.12.20 AS uv

FROM python:3.15-slim

COPY --from=uv /uv /usr/local/bin/uv

# The venv lives outside /app, so the dev compose file's bind mount of the
# checkout over /app cannot hide it. Always the image's own Python, never a download.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# uv installs everything, so the base image's pip is dead weight, and its
# vendored copies of urllib3 and friends are what the image scan flags.
RUN python -m pip uninstall --yes pip

# --locked: fail rather than resolve anew if uv.lock is out of step with pyproject.toml.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

# Hashed, compressed static files for WhiteNoise. Keep the ENV and collectstatic
# together: the ENV also selects the backend at runtime, so the manifest that is
# written is the one that is read. Needs no DB or secrets.
ENV STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
