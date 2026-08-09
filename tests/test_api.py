from __future__ import annotations

from fastapi.testclient import TestClient

from market_news_scraper.api import app


def test_shared_endpoints() -> None:
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    capabilities = client.get("/v1/capabilities").json()
    assert capabilities["collector"] == "market-news-scraper"
    assert capabilities["request_cadence_seconds"]["sec"] >= 0.11
    assert capabilities["request_cadence_seconds"]["issuer"] >= 0.25
    payload = {
        "request_id": "77777777-7777-4777-8777-777777777777",
        "query": {"kind": "ticker", "value": "ACME"},
        "requested_datasets": ["news"],
        "as_of": "2026-01-15T18:00:00Z",
        "source_settings": {"fixture_mode": True, "scenario": "normal"},
    }
    response = client.post("/v1/collect", json=payload)
    assert response.status_code == 200
    assert response.json()["records"][0]["kind"] == "news_event"
