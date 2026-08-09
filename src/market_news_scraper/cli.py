from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from market_data_contracts import CollectorRequest, QueryKind, SearchQuery

from .service import FIXTURE_SCENARIOS, NewsCollector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market-news")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capabilities")
    collect = subparsers.add_parser("collect")
    collect.add_argument("query")
    collect.add_argument(
        "--query-kind",
        choices=[item.value for item in QueryKind],
        default="ticker",
    )
    collect.add_argument("--fixture", choices=FIXTURE_SCENARIOS)
    collect.add_argument("--as-of", default=None, help="ISO-8601 timestamp; defaults to now")
    return parser


async def _run(args: argparse.Namespace) -> int:
    collector = NewsCollector()
    if args.command == "capabilities":
        print(json.dumps(collector.capabilities(), indent=2, sort_keys=True))
        return 0
    as_of = (
        datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        if args.as_of
        else datetime.now(UTC)
    )
    settings: dict[str, object] = {}
    if args.fixture:
        settings = {"fixture_mode": True, "scenario": args.fixture}
    request = CollectorRequest(
        request_id=uuid4(),
        query=SearchQuery(kind=args.query_kind, value=args.query),
        requested_datasets=("news", "filing_events"),
        as_of=as_of,
        source_settings=settings,
    )
    response = await collector.collect(request)
    print(response.model_dump_json(indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
