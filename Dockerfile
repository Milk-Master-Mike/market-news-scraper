FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MARKET_NEWS_CACHE_DIR=/var/cache/market-news

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir . && mkdir -p /var/cache/market-news && chown -R app:app /var/cache/market-news

USER app
EXPOSE 8103
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8103/health', timeout=2)"]
CMD ["uvicorn", "market_news_scraper.api:app", "--host", "0.0.0.0", "--port", "8103"]

