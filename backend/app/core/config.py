"""
ARIVIO Backend Configuration

Centralized configuration using pydantic-settings.
All secrets and environment-specific values are loaded from environment variables.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "ARIVIO"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "Personalized Product Intelligence Platform"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://arivio:arivio_dev@localhost:5432/arivio"
    DATABASE_ECHO: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT Auth
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173", 
        "http://localhost:3000",
        "http://192.168.1.8:5173",
        "http://192.168.1.8:5173"
    ]

    # AI Gateway
    # Supported providers: "openai", "gemini"/"google", "groq"
    AI_PROVIDER: str = "groq"
    AI_API_KEY: str = ""
    AI_MODEL: str = "openai/gpt-oss-20b"

    # OCR
    OCR_PROVIDER: str = "tesseract"

    # External Data Sources
    OPEN_FOOD_FACTS_API_URL: str = "https://world.openfoodfacts.org/api/v2"

    # Storage
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 10

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
