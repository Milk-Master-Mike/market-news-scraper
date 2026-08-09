from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from market_data_contracts import CollectorRequest, QueryKind, SearchQuery

from market_news_scraper.config import Settings
from market_news_scraper.service import NewsCollector


def settings(tmp_path: Path) -> Settings:
    return Settings(
        user_agent="market-news-tests/0.1 tests@example.test",
        cache_dir=tmp_path / "cache",
        cache_seconds=300,
        max_concurrency=2,
        request_timeout_seconds=2,
        sec_min_interval_seconds=0.2,
        issuer_min_interval_seconds=1.0,
        stale_after_days=30,
        manifest_path=Path(__file__).parents[1] / "config" / "issuer-feeds.json",
    )


def request(scenario: str) -> CollectorRequest:
    return CollectorRequest(
        request_id=UUID("77777777-7777-4777-8777-777777777777"),
        query=SearchQuery(kind=QueryKind.TICKER, value="ACME"),
        requested_datasets=("news", "filing_events"),
        as_of=datetime(2026, 1, 15, 18, tzinfo=UTC),
        source_settings={"fixture_mode": True, "scenario": scenario},
    )


@pytest.mark.parametrize("scenario", ["normal", "duplicates", "stale", "prompt_injection"])
async def test_success_scenarios_are_deterministic(tmp_path: Path, scenario: str) -> None:
    collector = NewsCollector(settings(tmp_path))
    first = await collector.collect(request(scenario))
    second = await collector.collect(request(scenario))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.run.status == "succeeded"
    assert first.records


async def test_duplicates_share_cluster_and_remain_cited(tmp_path: Path) -> None:
    response = await NewsCollector(settings(tmp_path)).collect(request("duplicates"))
    assert len(response.records) == 2
    assert len({record.duplicate_cluster_id for record in response.records}) == 1
    assert len({str(record.canonical_url) for record in response.records}) == 2
    assert all(record.evidence.source_url for record in response.records)


async def test_stale_event_is_retained_with_warning(tmp_path: Path) -> None:
    response = await NewsCollector(settings(tmp_path)).collect(request("stale"))
    assert response.records
    warnings = response.records[0].evidence.warnings
    assert any("freshness threshold" in warning for warning in warnings)


async def test_prompt_injection_is_normalized_as_untrusted_data(tmp_path: Path) -> None:
    response = await NewsCollector(settings(tmp_path)).collect(request("prompt_injection"))
    excerpt = response.records[0].evidence.excerpt or ""
    assert "system prompt" not in excerpt.lower()
    assert "<script>" not in excerpt
    assert any("untrusted" in warning for warning in response.records[0].evidence.warnings)


@pytest.mark.parametrize(
    ("scenario", "status", "code"),
    [
        ("malformed", "failed", "malformed"),
        ("unavailable", "failed", "unavailable"),
        ("rate_limited", "partial", "rate_limited"),
    ],
)
async def test_source_failures_are_structured_partial_results(
    tmp_path: Path, scenario: str, status: str, code: str
) -> None:
    response = await NewsCollector(settings(tmp_path)).collect(request(scenario))
    assert response.run.status == status
    assert any(failure.code == code for failure in response.partial_failures)
    if scenario == "rate_limited":
        assert response.records
