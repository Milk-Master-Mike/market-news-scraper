# Adding an issuer feed

Issuer feeds are denied by default. Add a feed only after a human reviews the
issuer-owned page, access terms, update behavior, and redistribution limits.

Each `config/issuer-feeds.json` entry has this shape:

```json
{
  "feed_id": "issuer-example",
  "url": "https://investor.example.com/news/feed.xml",
  "allowed_hosts": ["investor.example.com"],
  "company_id": "sec:0000000001",
  "ticker": "ACME",
  "publisher": "Acme Example Corporation",
  "reviewed_on": "2026-01-15",
  "review_note": "Issuer-owned RSS; metadata and short excerpts allowed",
  "enabled": true
}
```

Use an exact HTTPS URL and the smallest redirect-host list possible. Never add a
feed requiring a session, token, paywall, or CAPTCHA. Add sanitized fixtures and
tests before enabling it. Review enabled entries at least annually.

