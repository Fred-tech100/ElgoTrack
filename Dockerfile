# syntax=docker/dockerfile:1
# ---- Stage 1: compile Tailwind CSS ------------------------------------------------------
FROM node:22-alpine AS css
WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npx tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/app.css --minify

# ---- Stage 2: application image ----------------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
COPY --from=css /build/static/css/app.css /app/static/css/app.css
RUN chmod +x docker/entrypoint.sh scripts/*.sh \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/media /app/staticfiles && chown -R app:app /app/media /app/staticfiles
USER app
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "-c", "docker/gunicorn.conf.py"]
