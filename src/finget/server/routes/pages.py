"""页面路由 — 渲染 HTML 模板."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """仪表盘首页."""
    return templates.TemplateResponse(request, "dashboard.html", {"page": "dashboard"})


@router.get("/kline", response_class=HTMLResponse)
async def kline_page(request: Request):
    """K线分析页."""
    return templates.TemplateResponse(request, "kline.html", {"page": "kline"})


@router.get("/research", response_class=HTMLResponse)
async def research_page(request: Request):
    """研报中心页."""
    return templates.TemplateResponse(request, "research.html", {"page": "research"})


@router.get("/survey", response_class=HTMLResponse)
async def survey_page(request: Request):
    """机构调研页."""
    return templates.TemplateResponse(request, "survey.html", {"page": "survey"})


@router.get("/broker", response_class=HTMLResponse)
async def broker_page(request: Request):
    """券商金股页."""
    return templates.TemplateResponse(request, "broker.html", {"page": "broker"})
