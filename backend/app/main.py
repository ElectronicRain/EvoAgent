from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import PROJECT_ROOT, settings
from .db import close_db, init_db, session_scope
from .seed import seed_demo_data
from .services.extensions import extension_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("evoagent")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.prepare_directories()
    await init_db()
    await seed_demo_data()
    async with session_scope() as db:
        await extension_service.sync_skills(db)
    logger.info("EvoAgent %s started", settings.version)
    yield
    await close_db()


app = FastAPI(
    title="EvoAgent API",
    description="可进化多智能体协作平台",
    version=settings.version,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "service": settings.app_name, "version": settings.version}


@app.get("/")
async def root():
    dist = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if dist.exists():
        return FileResponse(dist)
    return {
        "name": "EvoAgent",
        "status": "running",
        "docs": "/docs",
        "message": "前端开发服务器默认运行在 http://localhost:5173",
    }


dist_dir = PROJECT_ROOT / "frontend" / "dist"
if dist_dir.exists():
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
