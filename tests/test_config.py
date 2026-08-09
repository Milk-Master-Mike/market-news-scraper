from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_news_scraper.config import Settings, load_manifest


def test_issuer_manifest_is_deny_by_default() -> None:
    manifest = load_manifest(Path(__file__).parents[1] / "config" / "issuer-feeds.json")
    assert manifest.feeds == ()


def test_default_manifest_is_independent_of_caller_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_manifest().feeds == ()


def test_issuer_feed_url_host_must_be_explicitly_allowlisted(tmp_path: Path) -> None:
    manifest = tmp_path / "feeds.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "feeds": [
                    {
                        "feed_id": "bad-host",
                        "url": "https://investor.example.test/feed.xml",
                        "allowed_hosts": ["cdn.example.test"],
                        "company_id": "sec:0000000001",
                        "ticker": "ACME",
                        "publisher": "Acme",
                        "reviewed_on": "2026-01-15",
                        "review_note": "Synthetic test",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not in allowed_hosts"):
        load_manifest(manifest)


def test_request_cadence_cannot_be_disabled_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_NEWS_SEC_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("MARKET_NEWS_ISSUER_MIN_INTERVAL_SECONDS", "0")
    settings = Settings.from_env()
    assert settings.sec_min_interval_seconds == 0.11
    assert settings.issuer_min_interval_seconds == 0.25
