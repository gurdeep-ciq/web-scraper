"""Harvest the exact store cookie the site sets when you pick a store by hand.

Our hand-crafted twm-userStoreInformation cookie didn't take — Total Wine
re-derives the store on load. So: open a permissive-state store page, click
"Shop this store" / "Make this my store" in the window, and this dumps the
resulting cookies so we can bake the real format into the session warm-up.

    python -m scraper.harvest_store            # opens TX Dallas (501)
    python -m scraper.harvest_store --url https://www.totalwine.com/store-info/florida-tampa/901

Steps:
  1. Browser opens the store page.
  2. Click the button that sets it as your store (solve Press & Hold if shown).
  3. Come back to the terminal and press Enter.
  4. It prints twm-userStoreInformation + related cookies.
"""

from __future__ import annotations

import argparse
import json
import pathlib

PROFILE_DIR = pathlib.Path("spike_out/px_profile_stealth")
DEFAULT = "https://www.totalwine.com/store-info/texas-dallas-park-lane/501"
KEYS = ("twm-userStoreInformation", "overrideStore", "twm-store", "storeId")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT)
    args = ap.parse_args()

    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), channel="chrome",
            headless=False, no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        print("\n>>> In the window: click 'Shop this store' / 'Make this my store'.")
        input(">>> Then press Enter here to dump cookies...")

        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        print("\n=== store-related cookies ===")
        for k in KEYS:
            if k in cookies:
                print(f"{k} = {cookies[k]}")
        (pathlib.Path("spike_out") / "harvested_store_cookies.json").write_text(
            json.dumps(cookies, indent=2)
        )
        print("\nFull cookie set saved to spike_out/harvested_store_cookies.json")
        ctx.close()


if __name__ == "__main__":
    main()
