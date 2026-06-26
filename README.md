# CMDS Extraction Service

Production service for educational PDF extraction, QC, and regeneration.
Ports the `extraction_engine_v4.html` prototype to a FastAPI backend + React frontend
with multi-OCR routing.

## Quick start (dev)

```bash
cp .env.example .env
# Set ANTHROPIC_API_KEY and generate ENCRYPTION_KEY:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up --build
```

Services:
- Backend API → http://localhost:8000  (`/docs` for OpenAPI)
- Frontend → http://localhost:5173
- MinIO console → http://localhost:9001 (minioadmin/minioadmin)
- Postgres → localhost:5432 (cmds/cmds)
- Redis → localhost:6379

## Repo layout

```
backend/                FastAPI service
  app/
    core/               config, db, redis, s3, claude client
    models/             SQLAlchemy ORM
    schemas/            Pydantic DTOs (incl. Block union)
    api/                FastAPI routers
    services/           business logic (analyser, extractor, regenerator, qc/)
    providers/          OCR providers (anthropic, mathpix, sarvam, google_vision)
    workers/            Celery tasks
  prompts/v1/           versioned prompt templates (verbatim from prompt library)
  alembic/              DB migrations
  tests/                pytest + golden master fixtures

frontend/               React + Vite + Tailwind + TanStack Query + Zustand
```

## Sprint status

- [x] Sprint 1 — scaffold, analyser, schema, upload, celery
- [ ] Sprint 2 — extraction pipeline + 7-check QC + LLM QC
- [ ] Sprint 3 — regeneration + frontend port
- [ ] Sprint 4 — multi-OCR + auth + deploy
