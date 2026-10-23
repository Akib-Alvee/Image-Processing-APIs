# Image Processing Service — Architecture & Step-by-Step Build Plan

A Cloudinary-inspired backend image processing service built with **Python (FastAPI + Pillow)**, **PostgreSQL**, **MinIO (S3-Compatible Storage)**, **RabbitMQ**, **Redis**, **Celery**, and **Docker Compose**.

---

## Current Build Progress (Continue from Step 5)

- [x] **Step 0: Architecture & Planning**
  - [x] Finalize tech stack (FastAPI, Pillow, PostgreSQL, MinIO, RabbitMQ, Redis, Celery)
  - [x] Design database schema & hybrid sync/async transformation flow
- [x] **Step 1: Project Scaffolding, Configuration & Docker Infrastructure**
  - [x] `requirements.txt` — Python dependencies
  - [x] `.env.example` — Environment variables template
  - [x] `Dockerfile` — Container image for FastAPI & Celery worker
  - [x] `docker-compose.yml` — PostgreSQL, Redis, RabbitMQ, MinIO (+ auto-bucket init), API & Worker
  - [x] `app/core/config.py` — Centralized Pydantic Settings (strictly from `.env`, no hardcoded secrets)
- [x] **Step 2: Database Setup, ORM Models & Migrations**
  - [x] `app/core/database.py` — Async & Sync SQLAlchemy engine & session factory
  - [x] `app/models/user.py` — `User` and `RefreshToken` models
  - [x] `app/models/image.py` — `Image` and `ImageTransformation` models
  - [x] `alembic.ini` & `alembic/env.py` & `alembic/versions/001_initial_schema.py` — Database migration setup
- [x] **Step 3: Authentication System (Access + Refresh Token Rotation)**
  - [x] `app/core/security.py` — Password hashing (Bcrypt) & JWT creation/verification
  - [x] `app/schemas/auth.py` — Pydantic request/response schemas for auth
  - [x] `app/api/deps.py` — `get_current_user` JWT guard dependency
  - [x] `app/api/routes/auth.py` — `/register` (with `IntegrityError` rollback), `/login`, `/refresh`, `/logout`
- [x] **Step 4: S3/MinIO Storage, Redis Cache, Rate Limiter & Image Upload/Retrieval**
  - [x] `app/services/storage.py` — `boto3` S3/MinIO adapter
  - [x] `app/schemas/image.py` — Pydantic schemas for image metadata & transformations
  - [x] `app/core/redis.py` & `app/core/rate_limit.py` — Redis cache & sliding-window rate limiter
  - [x] `app/api/routes/images.py` — Upload (`POST /images`), Get by ID (`GET /images/{id}`), Paginated List (`GET /images`)
- [/] **Step 5: Pillow Transformation Engine & Hybrid Celery/RabbitMQ Queue** *(NEXT STEP)*
  - [ ] `app/services/processor.py` — Pillow pipeline (`resize`, `crop`, `rotate`, `flip`, `mirror`, `watermark`, `compress`, `format`, `grayscale`, `sepia`)
  - [ ] `app/worker/celery_app.py` & `app/worker/tasks.py` — Celery + RabbitMQ broker + Redis backend
  - [ ] `app/services/image_service.py` — Sync/Async transformation orchestration (`POST /images/{id}/transform?async=false|true` & `GET /images/{id}/jobs/{job_id}`) & deduplication cache
- [ ] **Step 6: Main Application Wiring & End-to-End Verification**
  - [ ] `app/main.py` — FastAPI app, lifespan, routers, global error handling
  - [ ] `tests/` — Automated test suite & live verification

---

## Finalized Tech Stack

| Layer | Technology | Role & Rationale |
| :--- | :--- | :--- |
| **API Framework** | **FastAPI** + **Uvicorn** | High-performance async REST API with automatic OpenAPI/Swagger UI (`/docs`). |
| **Validation** | **Pydantic v2** | Strict request/response schema validation for auth and transformation payloads. |
| **Image Processing** | **Pillow (PIL)** | Core engine for resize, crop, rotate, flip, mirror, watermark, compress, format conversion, grayscale, and sepia filters. |
| **Database & ORM** | **PostgreSQL 16 (Docker)** + **SQLAlchemy 2.0 (Async)** + **Alembic** | Relational storage for users, refresh tokens, uploaded images, and transformed variants with schema migrations. |
| **Object Storage** | **MinIO (Docker)** via **boto3 (S3 API)** | Local S3-compatible object storage with Web Console (`:9001`). 100% compatible with Cloudflare R2 / AWS S3 via `.env` config. |
| **Auth & Security** | **PyJWT** + **Bcrypt** | Short-lived **Access Tokens** + long-lived **Refresh Tokens** (with token rotation & revocation stored in DB). |
| **Message Broker** | **RabbitMQ 3 Management (Docker)** | Dedicated AMQP message broker (`amqp://`) for queuing asynchronous Celery image transformation tasks, includes Management UI (`:15672`). |
| **Cache, Rate Limit & Backend** | **Redis 7 (Docker)** | Caching image metadata & idempotent transformation results, sliding-window rate limiting, and Celery result backend. |
| **Async Worker** | **Celery** (RabbitMQ Broker + Redis Backend) | Consumes transformation jobs from RabbitMQ and executes Pillow pipelines in background worker processes. |
| **Containerization** | **Docker & Docker Compose** | Orchestrates PostgreSQL, RabbitMQ, Redis, MinIO (with auto-bucket init), FastAPI server, and Celery worker. |

---

## API Endpoints Specification

### Authentication Endpoints (Completed in Step 3)
- `POST /register` — Register user, returns user + `access_token` + `refresh_token`.
- `POST /login` — Authenticate user, returns user + `access_token` + `refresh_token`.
- `POST /refresh` — Rotate Refresh Token and return a new `access_token` + `refresh_token`.
- `POST /logout` — Revoke Refresh Token.

### Image Management Endpoints (Completed in Step 4)
- `POST /images` — Multipart image upload, validates & extracts dimensions/format with Pillow, uploads to MinIO S3, stores metadata in PostgreSQL.
- `GET /images/{id}` — Retrieve image details/metadata (or stream/download image with optional `?raw=true` / `?format=`).
- `GET /images?page=1&limit=10` — Paginated list of user's uploaded images (cached in Redis).

### Image Transformation Endpoints (Step 5 — Next)
- `POST /images/{id}/transform?async=false` — Hybrid Sync/Async transformation endpoint with SHA-256 `transformation_hash` deduplication in Redis & PostgreSQL.
- `GET /images/{id}/jobs/{job_id}` — Poll status of async Celery transformation task.
