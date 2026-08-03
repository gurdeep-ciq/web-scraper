"""Warm patchright/Chrome session that fetches product data by navigating the
product page and intercepting the JSON the SPA fires itself.

Why this and not direct API calls: totalwine.com uses PerimeterX first-party
mode, which signs the page's own XHRs. Hand-issued requests (curl,
context.request, in-page fetch) all 403. Loading the page like a human and
capturing getProduct / reviews / summary responses is the reliable path
(verified in the Phase-0 spike).

One long-lived session handles many products; the persistent profile keeps the
PerimeterX token warm so the Press & Hold challenge is rare after the first solve.

Usage:
    with TotalWineSession() as s:
        data = s.fetch("https://www.totalwine.com/.../p/140521750")
        # data = {"product": {...}|None, "reviews": {...}|None, "summary": {...}|None}

Note: run HEADED (default). PerimeterX blocks headless; on a headless server use
a virtual display (xvfb-run) rather than headless=True.
"""

from __future__ import annotations

import time
from pathlib import Path

from .config import config

DEFAULT_PROFILE = Path("spike_out/px_profile_stealth")


class PXBlocked(Exception):
    """Raised when a product page could not clear PerimeterX."""


class TotalWineSession:
    def __init__(
        self,
        *,
        profile_dir: Path | str = DEFAULT_PROFILE,
        channel: str = "chrome",
        headless: bool = False,
        settle_ms: int = 6000,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.channel = channel
        self.headless = headless
        self.settle_ms = settle_ms
        self._pw = None
        self._ctx = None
        self._page = None
        self._cap: dict[str, dict] = {}

    # -- lifecycle ---------------------------------------------------------- #
    def __enter__(self) -> "TotalWineSession":
        from patchright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel=self.channel,
            headless=self.headless,
            no_viewport=True,
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._page.on("response", self._on_response)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -- interception ------------------------------------------------------- #
    def _on_response(self, resp) -> None:
        u = resp.url
        if resp.status != 200:
            return
        try:
            if "/getProduct/" in u:
                self._cap["product"] = resp.json()
            elif "/product-reviews/v1/products/" in u and "/reviews?" in u:
                self._cap["reviews"] = resp.json()
            elif "/reviews/summary" in u:
                self._cap["summary"] = resp.json()
        except Exception:
            pass

    # -- fetch -------------------------------------------------------------- #
    def fetch(self, url: str, *, retries: int = 1) -> dict:
        """Navigate to a product page and return the intercepted JSON payloads.

        Raises PXBlocked if getProduct never arrives (page stayed on a PX
        challenge). Caller decides whether to solve interactively or skip.
        """
        for attempt in range(retries + 1):
            self._cap = {}
            self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            self._page.wait_for_timeout(self.settle_ms)
            if "product" in self._cap:
                break
            if attempt < retries:
                self._page.wait_for_timeout(3000)
        if "product" not in self._cap:
            raise PXBlocked(url)

        # polite pacing between products
        time.sleep(config.request_delay_seconds)
        return {
            "product": self._cap.get("product"),
            "reviews": self._cap.get("reviews"),
            "summary": self._cap.get("summary"),
        }
