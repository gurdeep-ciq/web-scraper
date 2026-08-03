"""Shared HTTP headers for the curl-based sitemap/recon fetches.

The production data path goes through the browser session (browser.py); the only
thing shared with the curl fetches (sitemaps.py, recon.py) is a realistic
desktop-Chrome header set.
"""

from __future__ import annotations

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
