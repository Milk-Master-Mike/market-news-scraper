from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class IssuerFeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feed_id: str = Field(min_length=1)
    url: HttpUrl
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    company_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    reviewed_on: date
    review_note: str = Field(min_length=1)
    enabled: bool = False

    @field_validator("url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("issuer feeds must use HTTPS")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(host.lower().rstrip(".") for host in value)

    def validate_url_host(self) -> None:
        host = (urlsplit(str(self.url)).hostname or "").lower()
        if host not in self.allowed_hosts:
            raise ValueError(f"feed URL host {host!r} is not in allowed_hosts")


class FeedManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    feeds: tuple[IssuerFeed, ...] = ()


@dataclass(frozen=True)
class Settings:
    user_agent: str
    cache_dir: Path
    cache_seconds: int
    max_concurrency: int
    request_timeout_seconds: float
    sec_min_interval_seconds: float
    issuer_min_interval_seconds: float
    stale_after_days: int
    manifest_path: Path | None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            user_agent=os.getenv(
                "MARKET_NEWS_USER_AGENT",
                "market-news-scraper/0.1.0 contact@example.invalid",
            ),
            cache_dir=Path(os.getenv("MARKET_NEWS_CACHE_DIR", ".cache/http")),
            cache_seconds=max(0, int(os.getenv("MARKET_NEWS_CACHE_SECONDS", "300"))),
            max_concurrency=max(1, min(8, int(os.getenv("MARKET_NEWS_MAX_CONCURRENCY", "3")))),
            request_timeout_seconds=max(
                1.0, float(os.getenv("MARKET_NEWS_REQUEST_TIMEOUT_SECONDS", "10"))
            ),
            sec_min_interval_seconds=max(
                0.11,
                float(os.getenv("MARKET_NEWS_SEC_MIN_INTERVAL_SECONDS", "0.20")),
            ),
            issuer_min_interval_seconds=max(
                0.25,
                float(os.getenv("MARKET_NEWS_ISSUER_MIN_INTERVAL_SECONDS", "1.00")),
            ),
            stale_after_days=max(1, int(os.getenv("MARKET_NEWS_STALE_AFTER_DAYS", "30"))),
            manifest_path=(
                Path(os.environ["MARKET_NEWS_ISSUER_MANIFEST"])
                if "MARKET_NEWS_ISSUER_MANIFEST" in os.environ
                else None
            ),
        )


def load_manifest(path: Path | None = None) -> FeedManifest:
    if path is not None:
        payload = path.read_text(encoding="utf-8")
    else:
        packaged = resources.files("market_news_scraper").joinpath("data/issuer-feeds.json")
        if packaged.is_file():
            payload = packaged.read_text(encoding="utf-8")
        else:
            source_manifest = Path(__file__).resolve().parents[2] / "config" / "issuer-feeds.json"
            payload = source_manifest.read_text(encoding="utf-8")
    manifest = FeedManifest.model_validate(json.loads(payload))
    for feed in manifest.feeds:
        feed.validate_url_host()
    return manifest
