"""Application configuration via pydantic-settings (12-factor env).

Local-mode defaults are chosen so the service boots with no infra: SQLite DB,
local filesystem storage, and inline task execution. Switch to Postgres/S3/Celery
by overriding these in ``.env`` when running the Docker stack.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Look for .env in both the backend/ working dir and the repo root (one
    # level up) so the process works regardless of where it's launched from.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    APP_ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174"

    # Anthropic mode:
    #   "agent" — route calls through the Claude Agent SDK (uses the local
    #             ``claude`` CLI's OAuth / Max subscription — no API key).
    #   "real"  — hit the HTTP Anthropic API with ANTHROPIC_API_KEY.
    #   "mock"  — canned responses, no external calls.
    #   "auto"  — prefer agent if available, else real if key set, else mock.
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODE: Literal["auto", "mock", "real", "agent"] = "auto"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    ANTHROPIC_HAIKU_MODEL: str = "claude-haiku-4-5"

    @property
    def anthropic_effective_mode(self) -> str:
        if self.ANTHROPIC_MODE in ("agent", "real", "mock"):
            return self.ANTHROPIC_MODE
        # auto
        try:
            from app.core.claude_agent import is_available as _agent_available

            if _agent_available():
                return "agent"
        except Exception:
            pass
        try:
            from app.core.claude_client import has_real_key

            if has_real_key():
                return "real"
        except Exception:
            pass
        return "mock"

    @property
    def anthropic_use_mock(self) -> bool:
        return self.anthropic_effective_mode == "mock"

    @property
    def anthropic_use_agent(self) -> bool:
        return self.anthropic_effective_mode == "agent"

    # Database — defaults to SQLite under ./cmds.db for zero-infra local runs.
    # Railway / Heroku inject a generic "postgresql://..." (or sometimes the
    # legacy "postgres://...") DSN — SQLAlchemy's async engine needs the
    # explicit "+asyncpg" dialect, and the sync engine needs "+psycopg2".
    # We rewrite below so the deploy "just works" when only DATABASE_URL is
    # set in env.
    DATABASE_URL: str = "sqlite+aiosqlite:///./cmds.db"
    SYNC_DATABASE_URL: str = "sqlite:///./cmds.db"

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalize_async_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):       # Railway legacy form
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):     # add async dialect
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    @model_validator(mode="after")
    def _derive_sync_db_url(self) -> "Settings":
        # If SYNC_DATABASE_URL is still the SQLite default but the async URL
        # was switched to Postgres (Railway), derive the sync URL from the
        # async one so workers (which use SyncSession) can also connect.
        is_default_sync = self.SYNC_DATABASE_URL == "sqlite:///./cmds.db"
        if is_default_sync and self.DATABASE_URL.startswith("postgresql+asyncpg://"):
            self.SYNC_DATABASE_URL = (
                "postgresql+psycopg2://"
                + self.DATABASE_URL[len("postgresql+asyncpg://"):]
            )
        return self

    # Task executor — "inline" runs tasks in-process (no Celery needed);
    # "celery" dispatches to a worker over Redis.
    TASK_EXECUTOR: Literal["inline", "celery"] = "inline"

    # Redis / Celery (only used when TASK_EXECUTOR=celery)
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Storage — local filesystem by default; switch to "s3" for MinIO/S3
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_ROOT: str = "./storage"
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_PUBLIC_ENDPOINT: str = "http://localhost:8001"  # backend serves /storage in local mode
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_REGION: str = "us-east-1"
    S3_BUCKET_PDFS: str = "cmds-pdfs"
    S3_BUCKET_EXPORTS: str = "cmds-exports"
    S3_BUCKET_OCR_UPLOADS: str = "cmds-ocr-uploads"

    # Crypto — required to be set (Fernet). Bootstrap script generates one.
    ENCRYPTION_KEY: str = ""

    # Gemini — used for PDF extraction and regeneration
    GEMINI_API_KEY: str = ""
    GEMINI_REGEN_MODEL: str = "gemini-2.5-pro"

    # Question worker version. v2 = legacy excluded-block-driven (3-pass, has
    # the cross-section-duplication problem). v3 = section-aligned (mirrors
    # theory extractor; one Gemini call per schema section). Default v3.
    QUESTION_WORKER_VERSION: Literal["v2", "v3"] = "v3"

    # Phase 4 — multimodal question regen for questions with embedded images.
    # When ON: the regen call passes image bytes + DECISION RULE prompt; the
    # LLM also returns image_needs_regen verdict. When verdict=true, a figure
    # regeneration job is auto-enqueued using the new question as guidance.
    # Set FALSE to revert to text-only regen for ALL questions (rollback).
    # Only affects regen of image-bearing questions; text-only question
    # regen is identical regardless of this flag.
    MULTIMODAL_REGEN_ENABLED: bool = True

    # Step 2 — chained LaTeX/SVG diagram regen. When a regenerated question's
    # `regenerated_diagram.svg_preview` is present (and not fallback_to_original),
    # the Word export rasterizes that SVG to PNG and embeds it IN PLACE OF the
    # original figure. Primary rasterizer is cairosvg (Linux/prod: needs system
    # libcairo2). RESVG_BINARY_PATH is the dependency-free fallback for Windows
    # dev — point it at the resvg executable (empty = auto-detect on PATH and in
    # backend/tools/resvg/). Set EMBED_REGEN_DIAGRAM_IN_DOCX=False to keep the
    # original figure everywhere (rollback).
    EMBED_REGEN_DIAGRAM_IN_DOCX: bool = True
    RESVG_BINARY_PATH: str = ""

    # Engine-aware figure regeneration. When True, figure regen picks an engine
    # by semantic_type instead of always using the Gemini image model (which
    # garbles dense text): composite "table" figures get a crisp vector table
    # with the graphic embedded; diagrams/charts (schematics, flowcharts,
    # graphic organizers) get the LaTeX/SVG vector engine; illustrations/photos
    # keep the image-model redraw. Set False to restore the old behavior where
    # every figure goes through the image model (one-switch rollback).
    FIGURE_ENGINE_ROUTING_ENABLED: bool = True

    # Multi-OCR (Sprint 4; empty by default)
    MATHPIX_APP_ID: str = ""
    MATHPIX_APP_KEY: str = ""
    SARVAM_API_KEY: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
