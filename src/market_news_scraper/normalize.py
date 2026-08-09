from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from market_data_contracts import CatalystCategory, NewsEvent, SourceEvidence

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
INSTRUCTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions?", re.I),
    re.compile(r"(?:reveal|print|show)\s+(?:the\s+)?system\s+prompt", re.I),
    re.compile(r"(?:execute|run)\s+(?:this\s+)?command", re.I),
    re.compile(r"(?:call|invoke)\s+(?:a\s+)?tool", re.I),
)
CATEGORY_RULES: tuple[tuple[CatalystCategory, tuple[str, ...]], ...] = (
    (CatalystCategory.EARNINGS, ("earnings", "quarterly results", "financial results")),
    (CatalystCategory.GUIDANCE, ("guidance", "outlook", "forecast")),
    (CatalystCategory.CAPITAL, ("offering", "dividend", "repurchase", "financing")),
    (CatalystCategory.GOVERNANCE, ("director", "chief executive", "governance")),
    (CatalystCategory.PRODUCT, ("launch", "product", "clinical trial")),
    (CatalystCategory.REGULATORY, ("regulatory", "fda", "investigation")),
    (CatalystCategory.FILING, ("form 8-k", "form 10-k", "form 10-q", "files")),
    (CatalystCategory.MACRO, ("interest rate", "inflation", "tariff")),
)


class FeedParseError(ValueError):
    pass


def canonicalize_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("event URL must be an absolute HTTP(S) URL")
    host = parsed.hostname.lower().rstrip(".")
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    return urlunsplit((parsed.scheme.lower(), netloc, path, urlencode(sorted(query)), ""))


def sanitize_untrusted_text(raw: str, limit: int = 280) -> tuple[str, tuple[str, ...]]:
    without_markup = re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))
    cleaned = "".join(char if char >= " " else " " for char in without_markup)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    warnings: list[str] = []
    for pattern in INSTRUCTION_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("[redacted instruction-like text]", cleaned)
            if not warnings:
                warnings.append(
                    "Instruction-like source text was redacted; feed content is untrusted."
                )
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
        warnings.append(f"Excerpt truncated to {limit} characters.")
    return cleaned, tuple(warnings)


def classify_catalysts(title: str, excerpt: str) -> tuple[CatalystCategory, ...]:
    searchable = f"{title} {excerpt}".lower()
    matches = tuple(
        category for category, words in CATEGORY_RULES if any(w in searchable for w in words)
    )
    return matches or (CatalystCategory.OTHER,)


def _entry_datetime(raw: str | None, fallback: datetime) -> tuple[datetime, tuple[str, ...]]:
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed, ()
    return fallback, ("Feed entry had no parseable publication time; retrieval time was used.",)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ElementTree.Element, *names: str) -> str:
    wanted = set(names)
    for child in element:
        if _local_name(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _feed_entries(body: bytes) -> tuple[str, list[dict[str, str]]]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise FeedParseError(str(exc)) from exc
    root_name = _local_name(root.tag)
    if root_name == "feed":
        feed_title = _child_text(root, "title")
        elements = [child for child in root if _local_name(child.tag) == "entry"]
        entries: list[dict[str, str]] = []
        for item in elements:
            link = ""
            for child in item:
                is_link = _local_name(child.tag) == "link"
                is_alternate = child.attrib.get("rel", "alternate") == "alternate"
                if is_link and is_alternate:
                    link = child.attrib.get("href", "") or (child.text or "")
                    break
            entries.append(
                {
                    "id": _child_text(item, "id"),
                    "title": _child_text(item, "title"),
                    "link": link,
                    "published": _child_text(item, "published", "updated"),
                    "summary": _child_text(item, "summary", "content"),
                    "author": _child_text(item, "author"),
                }
            )
        return feed_title, entries
    if root_name == "rss":
        channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
        if channel is None:
            raise FeedParseError("RSS feed omitted channel")
        feed_title = _child_text(channel, "title")
        entries = []
        for item in channel:
            if _local_name(item.tag) != "item":
                continue
            entries.append(
                {
                    "id": _child_text(item, "guid"),
                    "title": _child_text(item, "title"),
                    "link": _child_text(item, "link"),
                    "published": _child_text(item, "pubdate", "date"),
                    "summary": _child_text(item, "description", "summary"),
                    "author": _child_text(item, "author", "creator"),
                }
            )
        return feed_title, entries
    raise FeedParseError(f"unsupported feed root: {root_name}")


def _association(
    text: str,
    query: str,
    company_id: str | None,
    ticker: str | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    needles = {query.casefold()}
    if ticker:
        needles.add(ticker.casefold())
    if not any(needle in text.casefold() for needle in needles):
        return (), ()
    companies = (company_id,) if company_id else ()
    instruments = (f"ticker:{ticker.upper()}",) if ticker else ()
    return companies, instruments


def parse_feed(
    body: bytes,
    *,
    source_name: str,
    source_url: str,
    query: str,
    company_id: str | None,
    ticker: str | None,
    publisher_override: str | None,
    retrieved_at: datetime,
    as_of: datetime,
    stale_after_days: int,
    parser_version: str,
) -> list[NewsEvent]:
    feed_title, entries = _feed_entries(body)

    records: list[NewsEvent] = []
    for entry in entries:
        raw_title = str(entry.get("title", "Untitled event"))
        title, title_warnings = sanitize_untrusted_text(raw_title, limit=180)
        excerpt, excerpt_warnings = sanitize_untrusted_text(
            str(entry.get("summary") or entry.get("description") or "")
        )
        association_text = f"{title} {excerpt}"
        companies, instruments = _association(association_text, query, company_id, ticker)
        if not companies and not instruments:
            continue
        try:
            canonical_url = canonicalize_url(str(entry.get("link", "")))
        except ValueError:
            continue
        published_at, date_warnings = _entry_datetime(entry.get("published"), retrieved_at)
        warnings = [*title_warnings, *excerpt_warnings, *date_warnings]
        if (as_of - published_at).days > stale_after_days:
            warnings.append(f"Event is older than the {stale_after_days}-day freshness threshold.")
        normalized_title = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
        cluster_seed = f"{normalized_title}|{published_at.date().isoformat()}"
        cluster_id = "cluster-" + hashlib.sha256(cluster_seed.encode()).hexdigest()[:16]
        event_seed = f"{canonical_url}|{published_at.isoformat()}|{source_name}"
        event_id = "news-" + hashlib.sha256(event_seed.encode()).hexdigest()[:20]
        raw_publisher = publisher_override or str(
            entry.get("author") or feed_title or source_name
        )
        publisher, publisher_warnings = sanitize_untrusted_text(raw_publisher, limit=120)
        warnings.extend(publisher_warnings)
        confidence = "0.75" if warnings else "0.95"
        evidence = SourceEvidence(
            evidence_id=f"ev-{event_id}",
            source_name=source_name,
            source_url=canonical_url,
            retrieved_at=retrieved_at,
            effective_date=published_at.date(),
            units="not_applicable",
            parser_version=parser_version,
            confidence=confidence,
            warnings=tuple(warnings),
            source_record_id=str(entry.get("id") or canonical_url),
            excerpt=excerpt or None,
        )
        records.append(
            NewsEvent(
                event_id=event_id,
                canonical_url=canonical_url,
                title=title,
                publisher=publisher,
                published_at=published_at,
                company_ids=companies,
                instrument_ids=instruments,
                catalyst_categories=classify_catalysts(title, excerpt),
                duplicate_cluster_id=cluster_id,
                content_hash=hashlib.sha256(association_text.encode()).hexdigest(),
                evidence=evidence,
            )
        )
    return records


def sort_timeline(records: list[NewsEvent]) -> list[NewsEvent]:
    return sorted(records, key=lambda item: (item.published_at, item.event_id), reverse=True)
