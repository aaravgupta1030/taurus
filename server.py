"""
Web API + static UI for the Creator Sourcing Agent.

Dev:  uvicorn server:app --reload --host 127.0.0.1 --port 8000
      (Writes to outputs/ run after the HTTP response so --reload does not kill a long /api/run.)
      cd ui && npm install && npm run dev   (Vite proxies /api → 8000; long timeout for /api)

Prod: cd ui && npm run build && uvicorn server:app --host 0.0.0.0 --port 8000
"""
import dataclasses
import logging
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.models import CreatorCandidate
from src.output_writer import write_outputs
from src.pipeline import run_pipeline
from src.utils import ROOT, load_env

# Ensure .env is loaded for early imports that read settings
load_env()

app = FastAPI(title="Creator Sourcing Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIST = ROOT / "ui" / "dist"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _write_outputs_safe(creators: List[CreatorCandidate]) -> None:
    """Run after HTTP response so --reload does not restart mid-request when outputs/ changes."""
    try:
        write_outputs(creators)
    except Exception:  # noqa: BLE001
        logging.exception("write_outputs failed")


class RunBody(BaseModel):
    query: str = Field(..., min_length=1, description="Brand or niche search")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/run")
def api_run(body: RunBody, background_tasks: BackgroundTasks):
    q = body.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    _setup_logging()
    try:
        creators = run_pipeline(q)
        payload = [dataclasses.asdict(c) for c in creators]
        background_tasks.add_task(_write_outputs_safe, creators)
        return {
            "ok": True,
            "query": q,
            "count": len(payload),
            "creators": payload,
        }
    except Exception as e:  # noqa: BLE001
        logging.exception("Pipeline failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/latest-files")
def latest_files():
    """Paths to on-disk outputs after last CLI or API run (same folder)."""
    out = ROOT / "outputs"
    return {
        "creators_json": str(out / "creators.json"),
        "creators_csv": str(out / "creators.csv"),
        "errors_log": str(out / "errors.log"),
    }


if UI_DIST.is_dir() and (UI_DIST / "index.html").exists():

    @app.get("/", include_in_schema=False)
    def spa_index():
        return FileResponse(UI_DIST / "index.html")

    assets_dir = UI_DIST / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="assets",
        )
else:

    @app.get("/")
    def ui_not_built():
        return JSONResponse(
            {
                "message": "Web UI not built yet. Run: cd ui && npm install && npm run build",
                "api_docs": "/docs",
                "health": "/api/health",
            }
        )

