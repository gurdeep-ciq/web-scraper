"""Capture the product page's background API calls (post-PX).

patchright clears PerimeterX, but totalwine.com is a client-side app: the
product HTML shell loads first, then JS fetches the real data (price, reviews,
availability) via XHR. This probe:

  - loads a product page in stealth Chrome (PX-cleared, persistent profile),
  - records EVERY response (url, status, content-type) and saves JSON bodies,
  - waits for network to settle, then dumps the fully rendered HTML too.

Read spike_out/network_log.txt to find the internal product/reviews/pricing
API endpoints — those become the real scraper targets (fast, no HTML parsing).

    python -m scraper.probe_network
    python -m scraper.probe_network --url <product-url> --wait 25
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

OUT = pathlib.Path("spike_out")
NET_DIR = OUT / "network"
PROFILE_DIR = OUT / "px_profile_stealth"  # reuse the solved profile
DEFAULT_URL = (
    "https://www.totalwine.com/spirits/bourbon/"
    "jim-beam-kentucky-fire-bourbon-whiskey/p/140521750"
)
INTERESTING = ("api", "product", "review", "price", "graphql", "search", "availab", "store")


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture post-PX product API calls")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--channel", default="chrome")
    ap.add_argument("--wait", type=int, default=25, help="seconds to let XHRs fire")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    NET_DIR.mkdir(exist_ok=True)

    from patchright.sync_api import sync_playwright

    log_lines: list[str] = []
    saved = 0

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel=args.channel,
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(resp):
            nonlocal saved
            url = resp.url
            ctype = ""
            try:
                ctype = resp.headers.get("content-type", "")
            except Exception:
                pass
            line = f"{resp.status:<4} {ctype[:30]:<30} {url}"
            log_lines.append(line)
            # Save JSON bodies from endpoints that look like data APIs.
            is_json = "json" in ctype or "graphql" in url
            if is_json and any(k in url.lower() for k in INTERESTING):
                try:
                    body = resp.text()
                except Exception:
                    return
                saved += 1
                fn = NET_DIR / f"resp_{saved:03d}.json"
                fn.write_text(
                    json.dumps({"url": url, "status": resp.status}) + "\n" + body,
                    encoding="utf-8",
                    errors="replace",
                )

        page.on("response", on_response)
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        print(f">>> Loaded shell; capturing XHRs for {args.wait}s...")
        # Let client-side fetches fire; nudge lazy content by scrolling.
        end = time.time() + args.wait
        while time.time() < end:
            try:
                page.mouse.wheel(0, 1200)
            except Exception:
                pass
            time.sleep(2)

        html = page.content()
        (OUT / "network_rendered.html").write_text(html, encoding="utf-8", errors="replace")
        (OUT / "network_log.txt").write_text("\n".join(log_lines), encoding="utf-8")

        print(f"\nResponses seen: {len(log_lines)} | JSON API bodies saved: {saved}")
        print("Interesting endpoints:")
        for ln in log_lines:
            if any(k in ln.lower() for k in INTERESTING) and ("json" in ln.lower() or "graphql" in ln.lower()):
                print("  " + ln)
        print(f"\nSaved: {OUT}/network_log.txt, {OUT}/network_rendered.html, {NET_DIR}/resp_*.json")
        input("\nPress Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()
