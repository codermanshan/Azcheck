"""HTTP helpers for amazon.ae public Best Sellers pages."""

from __future__ import annotations

import random
import time
from typing import Optional

import httpx

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-AE,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class AmazonClient:
    def __init__(self, timeout: float = 30.0, delay: float = 1.2) -> None:
        self.delay = delay
        self._client = httpx.Client(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AmazonClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.delay + random.uniform(0.2, 0.8)
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str) -> str:
        self._throttle()
        response = self._client.get(url)
        response.raise_for_status()
        text = response.text
        lower = text.lower()
        if "captcha" in lower and "robot check" in lower:
            raise RuntimeError(
                "Amazon showed a CAPTCHA / robot check. "
                "Wait a bit, use a VPN/home network, or lower --limit and retry."
            )
        return text
