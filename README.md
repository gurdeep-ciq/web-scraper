# Total Wine Scraper POC

Proof-of-concept scraper for **totalwine.com** product, pricing, review, and
store data — a replacement for the paid **Bright Data** feed. Feasibility is
**proven end to end** (see Phase-0 findings below).

## How it works (proven architecture)

totalwine.com is protected by **PerimeterX / HUMAN** (not Akamai). Plain
requests to any dynamic page return a 403 challenge. Two facts make scraping
possible anyway:

1. **Sitemaps are open** (plain curl, no bot check) and list the whole catalog:
   `sitemap.xml` → 17 `Product-en-USD-*.xml` (~5k URLs each ≈ **85k products**)
   plus `Store-en-USD.xml` (**217 stores**).
2. **PerimeterX is cleared with `patchright` + real Chrome.** Because PX runs in
   first-party mode (it signs the page's *own* XHRs), hand-issued API calls
   (curl / `context.request` / in-page `fetch`) all 403. The reliable path is to
   **load the product page like a human and intercept the JSON it fires itself**:
   - `…/getProduct/<sku>` → name, brand, price, rating, review count, size, stock
   - `…/product-reviews/v1/products/<id>/reviews` → customer reviews
   - `…/reviews/summary` → Total Wine's AI review summary

So: **enumerate SKUs from sitemaps → navigate each product page in a warm
patchright/Chrome session → intercept + validate + store.** Scale by running
several warm sessions / EC2 instances in parallel.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
patchright install chromium          # stealth browser runtime
cp .env.example .env
```
You also need **Google Chrome** installed (the session uses `channel="chrome"`).

## Run

```bash
docker compose up -d                 # local Postgres on host port 5433
python -m scraper.cli init-db        # create schema `web_scraping` + tables
python -m scraper.cli sync-stores    # load ~217 stores (curl, fast)
python -m scraper.cli run --limit 50 # scrape 50 products end-to-end (browser)
python -m scraper.cli run            # full catalog (~85k; long-running)
```

Run **headed** (a Chrome window opens). The first product may show a
"Press & Hold" challenge — solve it once; the persistent profile
(`spike_out/px_profile_stealth`) keeps the session warm afterwards. On a
headless server use `xvfb-run` rather than headless mode (PX blocks headless).

### Resilience & speed

- **Resumable**: a re-run skips products already in the DB and retries anything
  that was blocked, so you can stop/crash and just run `run` again to continue.
  (`--no-resume` to force a full re-scrape.)
- **PerimeterX-friendly by default**: a 1s jittered base pace, human-like
  mouse/scroll, and resources NOT blocked (real browsers load assets). On a
  block the session backs off, re-warms (human-like homepage browse), and an
  **adaptive throttle** ramps the pace up and keeps it elevated until PX cools
  off — this is what prevents the "Press & Hold every 1-2 items" loop on long
  runs. If PX still loops, the IP is flagged: pause ~15-30 min, or scale via
  distinct IPs (below).
- **`--fast`**: max speed (no pacing + block images/css/fonts, ~1.3s/product)
  for small test runs where PX-block risk doesn't matter. Also `--max-wait-ms`,
  `--delay-s` to tune.
- **Scale = distinct IPs** (PerimeterX gates concurrency per IP). One worker per
  IP:
  ```bash
  python -m scraper.cli run-parallel --limit 5000 \
      --proxies http://ip1:port http://ip2:port http://ip3:port
  ```
  This is the local stand-in for the EC2 fleet. Multiple workers on ONE IP just
  trigger PX challenges, so default is a single worker.

Inspect results:
```sql
SELECT source, count(*) FROM web_scraping.product GROUP BY 1;
SELECT category, count(*) FROM web_scraping.product GROUP BY 1 ORDER BY 2 DESC;
SELECT * FROM web_scraping.scrape_run ORDER BY id DESC LIMIT 5;
```

## Layout

| Path | Purpose |
|------|---------|
| `src/scraper/sitemaps.py` | catalog + store enumeration via open sitemaps (curl) |
| `src/scraper/browser.py` | `TotalWineSession`: patchright/Chrome navigate + intercept |
| `src/scraper/products.py` | parse `getProduct` → Product + Variant |
| `src/scraper/reviews.py` | parse reviews list + AI summary |
| `src/scraper/models.py` | SQLAlchemy tables + Pydantic validation schemas |
| `src/scraper/db.py` | engine, schema bootstrap, idempotent upserts |
| `src/scraper/pipeline.py` | orchestration + `scrape_run` tracking |
| `src/scraper/cli.py` | `init-db` / `sync-stores` / `run` |
| `src/scraper/recon.py`, `probe_*.py` | Phase-0 recon (how feasibility was established) |

## Migrating to staging

The DB target is one env var. Once Ashray grants Supabase access and posts the
staging Postgres URL, set `DB_URL` to it, keep `DB_SCHEMA=web_scraping` (a
**new** schema holding all retailers — don't touch existing ones), re-run
`init-db` then the pipeline. Every table carries a `source` column
(`totalwine` / `walmart` / `amazon`); pass `--source <name>` on `run`. Later,
`pipeline.run` wraps cleanly as a Dagster asset for the quarterly schedule.

## Notes / open questions for Ashray

- **Store**: pricing/availability come from the store the browser profile is
  pinned to (currently storeId 303 / NJ). Confirm which store to standardize on.
- **Throughput**: ~6–10s per product (full page load). 20–50k records = hours
  across a few parallel sessions; the ~85k full catalog wants the EC2 fleet.
- **Reviews depth**: currently the ~10 helpfulness-sorted reviews the page loads;
  can paginate deeper if needed.
- **Compliance**: robots.txt permits product pages; confirm ToS stance before a
  large-scale run.
