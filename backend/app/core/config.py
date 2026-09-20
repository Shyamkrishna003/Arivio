"""
Nirnavi Backend Configuration

Centralized configuration using pydantic-settings.
All secrets and environment-specific values are loaded from environment variables.
"""

import json

from pydantic import Field
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "Nirnavi"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "Personalized Product Intelligence Platform"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    #
    # A hosted provider's URL can be pasted in as given: the scheme is rewritten
    # to name asyncpg and libpq's TLS parameters are translated, in
    # app/db/url.py. Both this app and Alembic go through that.
    DATABASE_URL: str = "postgresql+asyncpg://arivio:arivio_dev@localhost:5432/arivio"
    DATABASE_ECHO: bool = False
    # Sized for a managed free tier, where the connection ceiling is far below
    # a local Postgres's and is often reached through a pooler that counts
    # every connection against it. Raise on a dedicated database.
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5
    # Set false behind a TRANSACTION-mode pooler — Supabase's port 6543,
    # PgBouncer in transaction pooling, Supavisor. Those hand a connection to a
    # different client between statements, so a prepared statement created on
    # one is missing on the next, and asyncpg fails with a
    # DuplicatePreparedStatementError or InvalidSQLStatementName that looks
    # nothing like a configuration problem. Session-mode poolers (port 5432)
    # and direct connections are fine as-is.
    DATABASE_PREPARED_STATEMENTS: bool = True

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT Auth
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    #
    # Both shapes are accepted, because both are what someone types into a
    # hosting dashboard:
    #
    #     CORS_ORIGINS=["https://nirnavi.vercel.app"]
    #     CORS_ORIGINS=https://nirnavi.vercel.app,https://www.nirnavi.app
    #
    # Only the first used to work. As a list[str] field, pydantic-settings
    # json-decodes the value inside the environment source — before any
    # validator of ours could see it — so a bare hostname aborted startup with
    # a parse error naming the field and not the fix. The very value shipped in
    # .env.example did it. Carried as a string and split by the property below.
    #
    # Read it as settings.CORS_ORIGINS; this raw field is only the transport.
    CORS_ORIGINS_RAW: str = Field(
        default="http://localhost:5173,http://localhost:3000,http://192.168.1.8:5173",
        validation_alias="CORS_ORIGINS",
    )

    # AI Gateway
    #
    # Model calls walk a chain and take the first success:
    #
    #     gemini ──▶ groq ──▶ cerebras ──▶ openrouter ──▶ rule-based fallback
    #
    # A provider with no key is skipped, not failed. The rule-based fallback
    # is reached only when every configured provider has failed, which is what
    # it was always meant for — before the chain existed, one transient 400
    # was enough to reach it.
    AI_PROVIDER_CHAIN: str = "gemini,groq,cerebras,openrouter"

    # Per-provider keys. Each provider only ever sees its own — a key is not
    # portable, and offering one to every provider just produces four 401s.
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # Per-provider model overrides. Deliberately not one global setting:
    # sending a Gemini model id to Groq fails at request time.
    GEMINI_MODEL: str = ""
    GEMINI_VISION_MODEL: str = ""
    GROQ_MODEL: str = ""
    GROQ_VISION_MODEL: str = ""
    CEREBRAS_MODEL: str = ""
    OPENROUTER_MODEL: str = ""
    OPENAI_MODEL: str = ""
    OPENAI_VISION_MODEL: str = ""

    # Per-attempt timeout, so the chain moves on rather than waiting out a
    # provider that has stopped answering. Kept tight because the primary
    # answers in ~3s — a dead provider should be abandoned quickly, not waited
    # out while a user stares at a spinner.
    AI_TIMEOUT: float = 30.0

    # ── Legacy single-provider settings ──
    # Still honoured: AI_API_KEY is used for whichever provider AI_PROVIDER
    # names, and that provider is appended to the chain if not already in it.
    # Prefer the per-provider keys above for new deployments.
    AI_PROVIDER: str = "groq"
    AI_API_KEY: str = ""
    AI_MODEL: str = "openai/gpt-oss-20b"

    # OCR / label reading
    #
    # "auto"      — use the vision model when a key is configured, else tesseract
    # "gemini"    — Google vision models (strongest cheap OCR)
    # "openai"    — GPT vision models
    # "groq"      — Groq vision models (fastest, weaker on dense panels)
    # "tesseract" — local, no API call, no key; weakest on real packaging
    OCR_PROVIDER: str = "auto"
    # Credentials for the vision model. Each falls back to the corresponding
    # AI_* setting when blank, so a single-provider deployment configures
    # nothing extra — but a deployment that wants label reading on Gemini and
    # report generation on Groq can say so.
    OCR_API_KEY: str = ""
    OCR_MODEL: str = ""
    # Vision calls are on a user's critical path but read a whole label, so
    # they get a longer budget than a text completion.
    OCR_TIMEOUT: float = 45.0
    # An extraction is cached on the image's content hash, so re-uploading the
    # same photo — the common case while a user retries a blurry shot — costs
    # one model call, not one per attempt.
    OCR_CACHE_TTL: int = 86400
    # How long a pending extraction stays retrievable for confirmation. Long
    # enough to survive a page reload and a careful review of the values.
    OCR_EXTRACTION_TTL: int = 3600
    # Uploaded label images are downscaled to this longest edge before being
    # sent or stored. Label text stays legible well below full sensor
    # resolution, and it cuts both the upload and the vision-model bill.
    OCR_MAX_IMAGE_EDGE: int = 1600

    # External Data Sources
    OPEN_FOOD_FACTS_API_URL: str = "https://world.openfoodfacts.org/api/v2"
    # Name search lives outside the versioned API, on the legacy CGI endpoint.
    OPEN_FOOD_FACTS_SEARCH_URL: str = "https://world.openfoodfacts.org/cgi/search.pl"
    # Open Food Facts asks every client to identify itself and throttles
    # generic agents. Override with real contact details in production.
    OPEN_FOOD_FACTS_USER_AGENT: str = "Nirnavi/0.1 (https://github.com/nirnavi)"
    # Name search is slower and more rate-limited than the barcode lookup, and
    # runs while a user waits — so it gets a tight timeout and a cache.
    OPEN_FOOD_FACTS_SEARCH_TIMEOUT: float = 6.0
    OPEN_FOOD_FACTS_SEARCH_CACHE_TTL: int = 3600

    # Storage
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 10

    # Health context (lab reports and similar documents)
    #
    # Fernet key for encrypting extracted health markers at rest. Generate with:
    #     python -c "from cryptography.fernet import Fernet; \
    #                print(Fernet.generate_key().decode())"
    #
    # The feature FAILS CLOSED: with no key set, every health endpoint answers
    # 503 and nothing is stored. That is deliberate — the alternative is
    # writing someone's blood test results to the database in plaintext
    # because a config value was forgotten.
    #
    # Losing this key makes existing health data permanently unreadable. That
    # is the intended property; back it up like a password, not like a config.
    HEALTH_ENCRYPTION_KEY: str = ""
    # Uploaded documents are parsed in memory and never written to disk, so
    # this only bounds what we accept into RAM.
    MAX_HEALTH_DOCUMENT_MB: int = 15
    # A document with more markers than this was almost certainly misparsed.
    MAX_HEALTH_MARKERS: int = 80

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        # So the field is also settable by its own name, which tests and
        # scripts constructing Settings() directly expect.
        "populate_by_name": True,
    }

    @property
    def CORS_ORIGINS(self) -> list[str]:
        """
        Browser origins allowed to call the API cross-origin.

        A trailing slash is trimmed: an Origin header never carries a path, so
        "https://nirnavi.vercel.app/" would match nothing and the symptom —
        every request blocked by the browser, the server reporting no error at
        all — points nowhere near the typo.
        """
        raw = self.CORS_ORIGINS_RAW.strip()
        if not raw:
            return []

        if raw.startswith("["):
            try:
                items = json.loads(raw)
            except json.JSONDecodeError as e:
                # Loud and specific: silently falling back to a comma split
                # would produce origins like '["https' that match nothing.
                raise ValueError(
                    f"CORS_ORIGINS looks like JSON but does not parse: {e}. "
                    "Fix the JSON, or write a plain comma-separated list."
                ) from e
        else:
            items = raw.split(",")

        return [str(item).strip().rstrip("/") for item in items if str(item).strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
