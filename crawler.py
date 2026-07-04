from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable, Iterator, List, Optional
from urllib.parse import urlparse

from playwright.sync_api import Error, TimeoutError, sync_playwright


@dataclass
class CrawlResult:
    url: str
    meta_title: str
    meta_description: str
    status: str
    error: str


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def normalize_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""

    if not _SCHEME_RE.match(url):
        url = f"https://{url}"

    return url


def extract_domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _extract_head_metadata(page) -> tuple[str, str]:
    # Read only document head metadata for output.
    return page.evaluate(
        """
        () => {
            const title = (document.querySelector('head > title')?.innerText || document.title || '').trim();
            const descNode =
              document.querySelector('head meta[name="description"]') ||
              document.querySelector('head meta[property="og:description"]') ||
              document.querySelector('head meta[name="twitter:description"]');
            const description = (descNode?.getAttribute('content') || '').trim();
            return [title, description];
        }
        """
    )


@lru_cache(maxsize=1)
def ensure_chromium_installed() -> None:
    """Install Chromium runtime once if it is not available in the environment."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
            return
    except Exception:
        pass

    # Fallback for hosted environments (for example Streamlit Cloud).
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
        capture_output=True,
        text=True,
    )


def crawl_urls(
    urls: Iterable[str],
    delay_per_domain_seconds: float = 2.0,
    timeout_ms: int = 20000,
    user_agent: Optional[str] = None,
) -> List[CrawlResult]:
    return list(
        crawl_urls_iter(
            urls,
            delay_per_domain_seconds=delay_per_domain_seconds,
            timeout_ms=timeout_ms,
            user_agent=user_agent,
        )
    )


def crawl_urls_iter(
    urls: Iterable[str],
    delay_per_domain_seconds: float = 2.0,
    timeout_ms: int = 20000,
    user_agent: Optional[str] = None,
) -> Iterator[CrawlResult]:
    results: List[CrawlResult] = []
    last_hit_by_domain: Dict[str, float] = {}

    normalized_urls = [normalize_url(u) for u in urls if (u or "").strip()]

    ensure_chromium_installed()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context_kwargs = {}
        if user_agent:
            context_kwargs["user_agent"] = user_agent

        context = browser.new_context(**context_kwargs)

        # Block non-essential resource types; we only need the head metadata.
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font"}
            else route.continue_(),
        )

        page = context.new_page()

        for url in normalized_urls:
            domain = extract_domain(url)
            if domain:
                last = last_hit_by_domain.get(domain)
                if last is not None:
                    elapsed = time.monotonic() - last
                    sleep_for = delay_per_domain_seconds - elapsed
                    if sleep_for > 0:
                        time.sleep(sleep_for)

            status_code = ""
            error = ""
            title = ""
            description = ""

            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if response is not None:
                    status_code = str(response.status)
                title, description = _extract_head_metadata(page)
            except TimeoutError:
                error = "Timeout"
            except Error as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)

            results.append(
                CrawlResult(
                    url=url,
                    meta_title=title,
                    meta_description=description,
                    status=status_code,
                    error=error,
                )
            )
            yield results[-1]

            if domain:
                last_hit_by_domain[domain] = time.monotonic()

        context.close()
        browser.close()

    return
