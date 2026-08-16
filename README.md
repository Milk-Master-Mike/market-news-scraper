# Market News Scraper

> **Archived:** This service now lives in [`market-research-platform`](https://github.com/Milk-Master-Mike/market-research-platform/tree/main/services/market-news). Its full Git history was preserved in the monorepo.

An independently runnable FastAPI service and CLI that collects company events
from SEC Atom/RSS sources and individually reviewed issuer investor-relations
feeds. It canonicalizes links, clusters duplicate stories, associates records
with the requested company/ticker, assigns transparent catalyst categories, and
returns a newest-first timeline using `market-data-contracts`.

This collector stores only event metadata and short evidence excerpts (maximum
280 characters) with links to the original source. It is not an article archive,
recommendation engine, return predictor, or trading system.

## Deterministic quick start

```bash
python -m pip install -e ".[dev]"
market-news capabilities
market-news collect ACME --fixture duplicates
uvicorn market_news_scraper.api:app --host 127.0.0.1 --port 8103
```

```bash
curl -X POST http://127.0.0.1:8103/v1/collect \
  -H "content-type: application/json" \
  --data @examples/collector-request.json
```

The service implements `GET /health`, `GET /v1/capabilities`, and
`POST /v1/collect`. Fixture scenarios are `normal`, `duplicates`, `stale`,
`malformed`, `rate_limited`, `unavailable`, and `prompt_injection`.

## Live source rules

SEC current-filings Atom is enabled by default and called with an identified
user agent from `MARKET_NEWS_USER_AGENT`. Set it to a real application/contact
value before live use. Issuer feeds are disabled unless their exact HTTPS URL,
company ID, ticker, publisher, review date, and terms-review note appear in
[`config/issuer-feeds.json`](config/issuer-feeds.json). Redirects to a host not
on that entry's allowed-host list are rejected.

Operational HTTP responses are cached separately under `MARKET_NEWS_CACHE_DIR`;
the collector has no user research database. Concurrency is bounded by
`MARKET_NEWS_MAX_CONCURRENCY` (default 3), requests use timeouts, and source
failures return alongside successful evidence.

Network starts are also serialized per destination host. SEC requests wait at
least `MARKET_NEWS_SEC_MIN_INTERVAL_SECONDS` after the preceding same-host
request completes (default `0.20`, never below `0.11`). Issuer-feed requests use
`MARKET_NEWS_ISSUER_MIN_INTERVAL_SECONDS` (default `1.00`, never below `0.25`).
Different hosts may proceed concurrently within the global bound, and fresh
cache hits make no network request. These cadence settings contain no secrets.

External titles and summaries are untrusted. HTML and control characters are
removed, instruction-like phrases are redacted and warned, and only normalized
records—not raw feed bodies—should be passed to any optional assistant.

## Development

```bash
pytest
ruff check .
```

CI is deterministic and performs no live scraping. See [SECURITY.md](SECURITY.md),
[`source-acceptance.yaml`](source-acceptance.yaml), and
[`docs/adding-an-issuer-feed.md`](docs/adding-an-issuer-feed.md).
