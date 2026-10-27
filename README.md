# Cloudinary-Inspired Image Processing API

A full-featured backend image processing service built with **Python (FastAPI + Pillow)**, **PostgreSQL 16**, **MinIO (S3-Compatible Storage)**, **RabbitMQ**, **Redis**, **Celery**, and **Docker Compose**.

---

## Features

- **JWT Authentication with Refresh Token Rotation:**
  - `POST /register` & `POST /login` issue short-lived **Access Tokens** (`15m`) and long-lived **Refresh Tokens** (`7d`).
  - `POST /refresh` rotates the Refresh Token (`jti` tracked in PostgreSQL) and revokes the old one.
  - `POST /logout` revokes active Refresh Tokens.
- **S3-Compatible Image Storage (MinIO / Cloudflare R2 / AWS S3):**
  - Validates uploaded files using Pillow binary magic-byte inspection.
  - Automatically extracts image dimensions (`width`, `height`), `format`, and `size_bytes`.
- **Rich Pillow Transformation Pipeline:**
  - **Crop** (`x`, `y`, `width`, `height` with boundary validation)
  - **Resize** (`width`, `height` with `LANCZOS` resampling)
  - **Rotate** (`-360°` to `360°` with canvas expansion)
  - **Flip** (vertical) & **Mirror** (horizontal)
  - **Filters**: **Grayscale** and **Sepia** (C-level color matrix)
  - **Watermark**: Semi-transparent text overlay with drop-shadow (`top-left`, `top-right`, `bottom-left`, `bottom-right`, `center`)
  - **Compress** (`quality: 1..100`) & **Format Conversion** (`jpeg`, `png`, `webp`)
- **Hybrid Sync / Async Processing + Caching & Rate Limiting:**
  - **SHA-256 Deduplication Cache**: Identical transformation requests return immediately from **Redis / PostgreSQL** without re-running Pillow.
  - **Sync Mode (`?async=false`)**: Runs Pillow immediately and returns `200 OK` with transformed image URL & metadata.
  - **Async Mode (`?async=true`)**: Publishes job to **RabbitMQ** for **Celery** background workers and returns `202 Accepted` with `job_id` polling at `GET /images/{id}/jobs/{job_id}`.
  - **Redis Rate Limiting**: Protects `/images` and `/images/{id}/transform` with `X-RateLimit-*` headers and `429 Too Many Requests`.

---

## Quick Start (Docker Compose)

1. **Copy `.env.example` to `.env`** and configure your local credentials:
   ```bash
   cp .env.example .env
   ```
2. **Start all 7 services** (PostgreSQL, Redis, RabbitMQ, MinIO + bucket init, FastAPI Server, Celery Worker):
   ```bash
   docker compose up --build -d
   ```
3. **Run Alembic database migrations** (optional, tables are also ensured on startup):
   ```bash
   docker compose exec api alembic upgrade head
   ```

---

## Service URLs & Dashboards

| Service | URL | Default Local Credentials |
| :--- | :--- | :--- |
| **FastAPI Swagger UI** | `http://localhost:8000/docs` | *(Use "Authorize" button with `access_token`)* |
| **MinIO S3 Console** | `http://localhost:9001` | Configured via `S3_ACCESS_KEY` / `S3_SECRET_KEY` in `.env` |
| **RabbitMQ Management UI** | `http://localhost:15672` | Configured via `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS` in `.env` |
| **Health Check** | `http://localhost:8000/health` | Public |

---

## Running Automated Tests

```bash
pytest -v
```
