from __future__ import annotations

from fastapi import FastAPI, HTTPException
from market_data_contracts import CollectorRequest, CollectorResponse

from . import __version__
from .service import NewsCollector

app = FastAPI(
    title="Market News Scraper",
    version=__version__,
    description="Allowlisted SEC and issuer-feed research evidence collector.",
)
collector = NewsCollector()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "collector": "market-news-scraper", "version": __version__}


@app.get("/v1/capabilities")
async def capabilities() -> dict[str, object]:
    return collector.capabilities()


@app.post("/v1/collect", response_model=CollectorResponse)
async def collect(request: CollectorRequest) -> CollectorResponse:
    try:
        return await collector.collect(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

