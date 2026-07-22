"""FastAPI 应用入口."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from finget.server.routes.api import router as api_router
from finget.server.routes.pages import router as pages_router
from finget.server.store_reader import get_store_reader

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时预热 store，关闭时清理."""
    get_store_reader()  # 预热连接
    yield
    reader = get_store_reader()
    reader.close()


def create_app() -> FastAPI:
    """创建 FastAPI 应用."""
    app = FastAPI(
        title="finget Dashboard",
        description="金融数据展示前端",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(pages_router)
    app.include_router(api_router, prefix="/api")

    # 静态资源（templates 下的 assets 目录，用于自定义 CSS/JS）
    assets_dir = _TEMPLATES_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    return app


app = create_app()
