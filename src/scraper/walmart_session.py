"""Warm patchright/Chrome session for walmart.com.

Same PerimeterX-passing technique as TotalWineSession, but Walmart is a Next.js
app: the product/browse data is server-rendered into a `__NEXT_DATA__` <script>
in the page HTML, so we just navigate and read that JSON (no XHR intercept).

Run HEADED (PX blocks headless); on a server use xvfb.
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

from .browser import PXBlocked

WM_HOME = "https://www.walmart.com/"
WM_PROFILE = Path("spike_out/walmart_profile")
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
# A real PX block (the app-id alone is in every page's sensor, so not a signal).
_BLOCK_MARKERS = ("px-captcha", "Robot or human", "/blocked?url=",
                  "Access to this page has been denied", "Activate and hold")


class WalmartSession:
    _BLOCK_TYPES = {"image", "media", "font", "stylesheet"}

    def __init__(self, *, profile_dir: Path | str = WM_PROFILE, channel: str = "chrome",
                 headless: bool = False, max_wait_ms: int = 15000, delay_s: float = 0.0,
                 human: bool = True, rewarm_ms: int = 8000, block_resources: bool = True,
                 store_id: str | None = None) -> None:
        self.profile_dir = Path(profile_dir)
        self.channel = channel
        self.headless = headless
        self.max_wait_ms = max_wait_ms
        self.delay_s = delay_s
        self.human = human
        self.rewarm_ms = rewarm_ms
        self.block_resources = block_resources
        self.store_id = store_id
        self._pw = self._ctx = self._page = None

    # -- lifecycle ---------------------------------------------------------- #
    def __enter__(self) -> "WalmartSession":
        from patchright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir), channel=self.channel,
            headless=self.headless, no_viewport=True)
        if self.block_resources:
            self._ctx.route("**/*", self._route)
        if self.store_id:
            self.set_location(self.store_id)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _route(self, route) -> None:
        try:
            if route.request.resource_type in self._BLOCK_TYPES:
                return route.abort()
            return route.continue_()
        except Exception:
            try:
                return route.continue_()
            except Exception:
                return None

    def set_location(self, assortment_store_id: str) -> None:
        """Pin an alcohol-permitting location (Walmart gates alcohol by store)."""
        self._ctx.add_cookies([
            {"name": "assortmentStoreId", "value": assortment_store_id,
             "domain": ".walmart.com", "path": "/"},
            {"name": "hasLocData", "value": "1", "domain": ".walmart.com", "path": "/"},
        ])

    # -- helpers ------------------------------------------------------------ #
    def _fidget(self) -> None:
        if not self.human:
            return
        try:
            self._page.mouse.move(random.randint(120, 900), random.randint(120, 600))
            self._page.wait_for_timeout(random.randint(150, 450))
            self._page.mouse.wheel(0, random.randint(400, 1100))
        except Exception:
            pass

    @staticmethod
    def _blocked(html: str) -> bool:
        return any(m in html for m in _BLOCK_MARKERS)

    def warm_up(self, max_seconds: int = 60) -> bool:
        try:
            self._page.goto(WM_HOME, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
        self._fidget()
        self._page.wait_for_timeout(self.rewarm_ms)
        end = time.time() + max_seconds
        while True:
            if not self._blocked(self._safe_content()):
                return True
            if time.time() >= end:
                return False
            self._page.wait_for_timeout(2000)

    def rewarm(self) -> bool:
        try:
            self._page.goto(WM_HOME, wait_until="domcontentloaded", timeout=60_000)
            self._page.wait_for_timeout(self.rewarm_ms)
            self._fidget()
            return not self._blocked(self._safe_content())
        except Exception:
            return False

    def _safe_content(self) -> str:
        try:
            return self._page.content()
        except Exception:
            return ""

    # -- fetch -------------------------------------------------------------- #
    def load(self, url: str) -> str:
        """Navigate and return HTML once __NEXT_DATA__ is present. Raise
        PXBlocked if the page is challenged or never yields the data."""
        self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self._fidget()
        end = time.time() + self.max_wait_ms / 1000
        html = ""
        while time.time() < end:
            html = self._safe_content()
            if self._blocked(html):
                raise PXBlocked(url)
            if "__NEXT_DATA__" in html:
                if self.delay_s:
                    time.sleep(self.delay_s)
                return html
            self._page.wait_for_timeout(500)
        raise PXBlocked(url)

    def next_data(self, url: str) -> dict:
        m = _NEXT_RE.search(self.load(url))
        if not m:
            raise PXBlocked(url)
        return json.loads(m.group(1))

    def product(self, us_item_id: str) -> dict:
        return self.next_data(f"{WM_HOME}ip/{us_item_id}")

    def reviews(self, us_item_id: str) -> dict:
        """__NEXT_DATA__ of the dedicated reviews page (full customerReviews)."""
        return self.next_data(f"{WM_HOME}reviews/product/{us_item_id}")
