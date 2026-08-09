from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from market_data_contracts import (
    CollectorRequest,
    CollectorResponse,
    FailureCode,
    NewsEvent,
    PartialFailure,
    QueryKind,
    RunMode,
    RunStatus,
    ScrapeRun,
)

from . import __version__
from .config import IssuerFeed, Settings, load_manifest
from .http import CachedFeedClient, SourceHTTPError
from .normalize import FeedParseError, parse_feed, sort_timeline

SEC_ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom"
FIXTURE_SCENARIOS = (
    "normal",
    "duplicates",
    "stale",
    "malformed",
    "rate_limited",
    "unavailable",
    "prompt_injection",
)


@dataclass(frozen=True)
class FeedSource:
    source_id: str
    name: str
    url: str
    allowed_hosts: frozenset[str]
    company_id: str | None = None
    ticker: str | None = None
    publisher: str | None = None


class NewsCollector:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.manifest = load_manifest(self.settings.manifest_path)
        self.client = CachedFeedClient(
            cache_dir=self.settings.cache_dir,
            cache_seconds=self.settings.cache_seconds,
            user_agent=self.settings.user_agent,
            timeout_seconds=self.settings.request_timeout_seconds,
            max_concurrency=self.settings.max_concurrency,
            sec_min_interval_seconds=self.settings.sec_min_interval_seconds,
            issuer_min_interval_seconds=self.settings.issuer_min_interval_seconds,
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "collector": "market-news-scraper",
            "version": __version__,
            "contract_version": "0.1.0",
            "datasets": ["news", "filing_events"],
            "sources": ["sec_current_filings_atom", "allowlisted_issuer_ir_feeds"],
            "fixture_scenarios": list(FIXTURE_SCENARIOS),
            "features": [
                "url_canonicalization",
                "duplicate_clustering",
                "company_ticker_association",
                "catalyst_categories",
                "newest_first_timeline",
                "partial_failures",
                "serialized_per_host_cadence",
            ],
            "request_cadence_seconds": {
                "sec": self.settings.sec_min_interval_seconds,
                "issuer": self.settings.issuer_min_interval_seconds,
            },
            "credentials_required": False,
        }

    def _fixture_bytes(self, scenario: str) -> bytes:
        filename = f"{scenario}.xml"
        packaged = resources.files("market_news_scraper").joinpath(f"fixtures/{filename}")
        if packaged.is_file():
            return packaged.read_bytes()
        source_fixture = Path(__file__).resolve().parents[2] / "fixtures" / filename
        return source_fixture.read_bytes()

    def _issuer_sources(self, request: CollectorRequest) -> list[FeedSource]:
        query = request.query.value.casefold()
        company_id = request.resolved_entity.company_id if request.resolved_entity else None
        sources: list[FeedSource] = []
        for feed in self.manifest.feeds:
            if not feed.enabled:
                continue
            query_matches = query in {feed.ticker.casefold(), feed.publisher.casefold()}
            if not query_matches and company_id != feed.company_id:
                continue
            sources.append(self._from_issuer_feed(feed))
        return sources

    @staticmethod
    def _from_issuer_feed(feed: IssuerFeed) -> FeedSource:
        return FeedSource(
            source_id=feed.feed_id,
            name=f"{feed.publisher} investor relations",
            url=str(feed.url),
            allowed_hosts=frozenset(feed.allowed_hosts),
            company_id=feed.company_id,
            ticker=feed.ticker,
            publisher=feed.publisher,
        )

    async def _parse_source(
        self,
        source: FeedSource,
        request: CollectorRequest,
        retrieved_at: datetime,
        body: bytes | None = None,
    ) -> tuple[list[NewsEvent], PartialFailure | None]:
        try:
            payload = (
                body
                if body is not None
                else await self.client.get(source.url, set(source.allowed_hosts))
            )
            ticker = source.ticker
            if ticker is None and request.query.kind == QueryKind.TICKER:
                ticker = request.query.value
            company_id = source.company_id
            if company_id is None and request.resolved_entity:
                company_id = request.resolved_entity.company_id
            records = parse_feed(
                payload,
                source_name=source.name,
                source_url=source.url,
                query=request.query.value,
                company_id=company_id,
                ticker=ticker,
                publisher_override=source.publisher,
                retrieved_at=retrieved_at,
                as_of=request.as_of,
                stale_after_days=self.settings.stale_after_days,
                parser_version=__version__,
            )
            return records, None
        except FeedParseError as exc:
            return [], self._failure(
                source, "malformed", f"Malformed feed: {exc}", False, retrieved_at
            )
        except SourceHTTPError as exc:
            return [], self._failure(source, exc.code, str(exc), exc.retryable, retrieved_at)
        except (OSError, ValueError) as exc:
            return [], self._failure(
                source,
                "internal",
                f"Source could not be normalized: {exc}",
                False,
                retrieved_at,
            )

    @staticmethod
    def _failure(
        source: FeedSource,
        code: str,
        message: str,
        retryable: bool,
        occurred_at: datetime,
    ) -> PartialFailure:
        digest = hashlib.sha256(f"{source.source_id}|{code}|{message}".encode()).hexdigest()[:16]
        return PartialFailure(
            failure_id=f"news-failure-{digest}",
            source_name=source.name,
            dataset="news",
            code=FailureCode(code),
            message=message,
            occurred_at=occurred_at,
            retryable=retryable,
            source_url=source.url,
            warnings=("Evidence from other available sources is retained.",),
        )

    async def collect(self, request: CollectorRequest) -> CollectorResponse:
        fixture_mode = bool(request.source_settings.get("fixture_mode", False))
        scenario = str(request.source_settings.get("scenario", "normal"))
        if fixture_mode and scenario not in FIXTURE_SCENARIOS:
            raise ValueError(f"unknown fixture scenario: {scenario}")
        started_at = request.as_of if fixture_mode else datetime.now(UTC)
        records: list[NewsEvent] = []
        failures: list[PartialFailure] = []
        source_names: list[str] = []

        if fixture_mode:
            source = FeedSource(
                source_id=f"fixture-{scenario}",
                name=f"Deterministic {scenario} feed",
                url=f"https://fixtures.example.test/market-news/{scenario}.xml",
                allowed_hosts=frozenset({"fixtures.example.test"}),
                company_id=(
                    request.resolved_entity.company_id if request.resolved_entity else None
                ),
                ticker=request.query.value if request.query.kind == QueryKind.TICKER else None,
                publisher="Fixture Newswire",
            )
            source_names.append(source.name)
            if scenario == "unavailable":
                failures.append(
                    self._failure(
                        source, "unavailable", "Deterministic source outage.", True, started_at
                    )
                )
            elif scenario == "rate_limited":
                parsed, failure = await self._parse_source(
                    source, request, started_at, self._fixture_bytes("normal")
                )
                records.extend(parsed)
                if failure:
                    failures.append(failure)
                failures.append(
                    self._failure(
                        source,
                        "rate_limited",
                        "Deterministic HTTP 429 response.",
                        True,
                        started_at,
                    )
                )
            else:
                parsed, failure = await self._parse_source(
                    source, request, started_at, self._fixture_bytes(scenario)
                )
                records.extend(parsed)
                if failure:
                    failures.append(failure)
        else:
            sources: list[FeedSource] = []
            if bool(request.source_settings.get("include_sec", True)):
                sec = FeedSource(
                    source_id="sec-current-filings",
                    name="SEC current filings Atom",
                    url=SEC_ATOM_URL,
                    allowed_hosts=frozenset({"www.sec.gov", "sec.gov"}),
                )
                if "example.invalid" in self.settings.user_agent:
                    failures.append(
                        self._failure(
                            sec,
                            "blocked",
                            "Set MARKET_NEWS_USER_AGENT to an identified "
                            "application/contact before live SEC use.",
                            False,
                            started_at,
                        )
                    )
                else:
                    sources.append(sec)
            if bool(request.source_settings.get("include_issuer_feeds", True)):
                sources.extend(self._issuer_sources(request))
            source_names.extend(source.name for source in sources)
            results = await asyncio.gather(
                *(self._parse_source(source, request, started_at) for source in sources)
            )
            for parsed, failure in results:
                records.extend(parsed)
                if failure:
                    failures.append(failure)

        records = sort_timeline(records)
        if records and failures:
            status = RunStatus.PARTIAL
        elif failures:
            status = RunStatus.FAILED
        else:
            status = RunStatus.SUCCEEDED
        finished_at = started_at + timedelta(seconds=1) if fixture_mode else datetime.now(UTC)
        run_id = (
            uuid5(NAMESPACE_URL, f"{request.request_id}:{scenario}") if fixture_mode else uuid4()
        )
        run = ScrapeRun(
            run_id=run_id,
            collector="market-news-scraper",
            collector_version=__version__,
            mode=RunMode.FIXTURE if fixture_mode else RunMode.LIVE,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            query=request.query,
            requested_datasets=request.requested_datasets,
            source_names=tuple(source_names),
            warnings=("Timeline is metadata and evidence, not investment advice.",),
        )
        return CollectorResponse(
            run=run,
            records=tuple(records),
            partial_failures=tuple(failures),
        )
