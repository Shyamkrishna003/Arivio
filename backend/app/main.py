"""
ARIVIO — Personalized Product Intelligence Platform

Main FastAPI application entry point.
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.auth.router import router as auth_router
from app.users.router import router as users_router
from app.products.router import router as products_router
from app.personalization.router import router as personalization_router
from app.ai.router import router as ai_router
from app.community.router import router as community_router
from app.ocr.router import router as ocr_router
from app.health.router import router as health_router

# Import all models to ensure SQLAlchemy mapper registry is fully populated
import app.community.models
import app.ingredients.models
import app.users.models
import app.products.models
import app.ai.models
import app.health.models

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup & shutdown."""
    # Startup
    print(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} starting...")
    yield
    # Shutdown
    print(f"🛑 {settings.APP_NAME} shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"^https://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+):5173$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes
app.include_router(auth_router, prefix=settings.API_PREFIX)
app.include_router(users_router, prefix=settings.API_PREFIX)
app.include_router(products_router, prefix=settings.API_PREFIX)
app.include_router(personalization_router, prefix=settings.API_PREFIX)
app.include_router(ai_router, prefix=settings.API_PREFIX)
app.include_router(community_router, prefix=settings.API_PREFIX)
app.include_router(ocr_router, prefix=settings.API_PREFIX)
app.include_router(health_router, prefix=settings.API_PREFIX)

# Uploaded label photos. Served from the app for now, which is fine at this
# scale; the PRD's object storage is the eventual home, and moving there only
# changes the URL these are stored with.
#
# Created eagerly so mounting cannot fail on a fresh checkout, and mounted
# outside API_PREFIX because these are files, not API resources. Filenames are
# content hashes, so nothing here is guessable from a product id — but note
# the directory is public: it must only ever hold product packaging, never a
# document from the health-context feature.
_uploads_dir = os.path.abspath(settings.UPLOAD_DIR)
try:
    os.makedirs(_uploads_dir, exist_ok=True)
except OSError as e:
    # A directory we cannot create is a broken scan feature, not a broken
    # application — every other route still works, and the upload endpoint
    # answers 503 with an explanation. check_dir=False keeps the mount from
    # raising here and taking the whole app down with it.
    print(f"⚠️ Upload directory {_uploads_dir} is unavailable: {e}")
app.mount(
    "/uploads",
    StaticFiles(directory=_uploads_dir, check_dir=False),
    name="uploads",
)


@app.get("/")
async def root():
    """Health check."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "operational",
        "tagline": "Look Beyond the Label",
    }


@app.get("/health")
async def health():
    """Detailed health check."""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
    }
