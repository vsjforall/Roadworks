"""
Read API. The phone talks to this and nothing else.

Deliberately thin. Every expensive decision — formatting, variance, district
canonicalisation, confidence filtering — already happened during extraction.
This layer only selects rows. That is what keeps the feed fast on a bad
mobile connection in Kerala, which is the network this actually runs on.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .store import Store

WEB = Path(__file__).parent / "web"

app = FastAPI(title="Kerala infra feed", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
    allow_headers=["*"],
)

store = Store(os.environ.get("KI_DB", "kerala_infra.db"))


@app.get("/api/feed")
def feed(
    filter: str = Query("all", pattern="^(all|nh|bridge|big|awarded)$"),
    district: str | None = None,
    limit: int = Query(60, le=200),
):
    rows = store.query(kind=filter, district=district, limit=limit)
    return {"count": len(rows), "items": rows}


@app.get("/api/districts")
def districts():
    return {"items": store.district_counts()}


@app.get("/api/item/{tender_id}")
def item(tender_id: str):
    return store.get(tender_id) or {"error": "not found"}


@app.get("/api/health")
def health():
    return {"ok": True, "records": store.count()}


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/sw.js")
def sw():
    return FileResponse(WEB / "sw.js", media_type="application/javascript")


@app.get("/manifest.json")
def manifest():
    return FileResponse(WEB / "manifest.json")
