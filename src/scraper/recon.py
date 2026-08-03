"""One-off recon probe (Phase-0 follow-up).

Directly fetches sitemap + a category/product URL with curl_cffi and saves the
FULL raw body (including non-200) so we can see exactly what Akamai returns and
whether the sitemap lets us enumerate the catalog without the browse pages.
"""

from __future__ import annotations

import pathlib

from curl_cffi import requests as cffi

from .fetch import DEFAULT_HEADERS

OUT = pathlib.Path("spike_out")
OUT.mkdir(exist_ok=True)

TARGETS = {
    # A real product page from the sitemap (does PX block product detail too?)
    "product_page": "https://www.totalwine.com/spirits/bourbon/jim-beam-kentucky-fire-bourbon-whiskey/p/140521750",
    # Candidate internal data APIs (guesses to probe for JSON that isn't PX-gated)
    "api_products_v2": "https://www.totalwine.com/api/products/v2/140521750",
    "api_product": "https://www.totalwine.com/product/api/v1/product/140521750",
}


def probe(name: str, url: str) -> None:
    try:
        r = cffi.get(url, headers=DEFAULT_HEADERS, impersonate="chrome124", timeout=30)
        body = r.text
        status = r.status_code
        ctype = r.headers.get("content-type", "")
    except Exception as e:  # noqa: BLE001
        body, status, ctype = f"ERROR {type(e).__name__}: {e}", -1, ""
    (OUT / f"recon_{name}.txt").write_text(body, encoding="utf-8", errors="replace")
    print(f"{name:16} status={status:<4} type={ctype:<30} bytes={len(body)}")


if __name__ == "__main__":
    for n, u in TARGETS.items():
        probe(n, u)
    print(f"\nBodies in ./{OUT}/recon_*.txt")
