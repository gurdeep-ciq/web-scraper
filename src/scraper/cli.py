"""Command-line entrypoint.

    python -m scraper.cli init-db                 # create schema + tables
    python -m scraper.cli sync-stores             # load store directory (curl)
    python -m scraper.cli run --limit 200         # scrape product data (browser)
    python -m scraper.cli run                      # full catalog (~85k)
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create schema + tables")
    sub.add_parser("sync-stores", help="load the store directory from the sitemap")

    p_run = sub.add_parser("run", help="scrape product data from the sitemaps")
    p_run.add_argument("--limit", type=int, default=None, help="max products")
    p_run.add_argument("--max-sitemaps", type=int, default=None,
                       help="cap number of product sitemaps scanned")
    p_run.add_argument("--settle-ms", type=int, default=6000,
                       help="ms to wait per page for XHRs to fire")

    args = ap.parse_args()

    if args.cmd == "init-db":
        from .db import init_db

        init_db()
        print("schema + tables created")
    elif args.cmd == "sync-stores":
        from .pipeline import sync_stores

        print({"stores": sync_stores()})
    elif args.cmd == "run":
        from .pipeline import run

        print(run(limit=args.limit, max_sitemaps=args.max_sitemaps, settle_ms=args.settle_ms))


if __name__ == "__main__":
    main()
