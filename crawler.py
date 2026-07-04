from __future__ import annotations

import html as html_lib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable, Iterator, List, Optional
from urllib.parse import urlparse

import requests


@dataclass
class CrawlResult:
    url: str
    meta_title: str
    meta_description: str
    status: str
    error: str


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HEAD_RE = re.compile(r"<head[^>]*>(.*?)</head>", re.IGNORECASE | re.DOTALL)


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


def _extract_metadata_from_html(raw_html: str) -> tuple[str, str]:
    html_text = raw_html or ""
    head_match = _HEAD_RE.search(html_text)
    head_html = head_match.group(1) if head_match else html_text

    title_match = _TITLE_RE.search(head_html)
    title = html_lib.unescape(title_match.group(1).strip()) if title_match else ""

    description = ""
    meta_patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\'][^>]*>',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\'][^>]*>',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\'][^>]*>',
        r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\'](.*?)["\'][^>]*>',
    ]
    for pattern in meta_patterns:
        match = re.search(pattern, head_html, re.IGNORECASE | re.DOTALL)
        if match:
            description = html_lib.unescape(match.group(1).strip())
            break

    return title, description


@lru_cache(maxsize=1)
def ensure_chromium_installed(sync_playwright) -> None:
    """Install Chromium runtime once if it is not available in the environment."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
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


@lru_cache(maxsize=1)
def ensure_playwright_package_installed() -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "playwright"],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_playwright(timeout_ms: int, user_agent: Optional[str]):
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        try:
            ensure_playwright_package_installed()
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception:
            return None, None, None, None, f"Playwright import failed: {exc}"

    try:
        ensure_chromium_installed(sync_playwright)
        p = sync_playwright().start()
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )

        context_kwargs = {}
        if user_agent:
            context_kwargs["user_agent"] = user_agent

        context = browser.new_context(**context_kwargs)
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font"}
            else route.continue_(),
        )
        page = context.new_page()
        page.set_default_navigation_timeout(timeout_ms)
        return (p, browser, context, page, None, PlaywrightError, PlaywrightTimeoutError)
    except Exception as exc:
        return None, None, None, None, f"Playwright launch failed: {exc}"


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
    last_hit_by_domain: Dict[str, float] = {}

    normalized_urls = [normalize_url(u) for u in urls if (u or "").strip()]

    p = None
    browser = None
    context = None
    page = None
    playwright_error_type = Exception
    playwright_timeout_type = Exception

    init = _init_playwright(timeout_ms=timeout_ms, user_agent=user_agent)
    if len(init) == 7:
        p, browser, context, page, init_error, playwright_error_type, playwright_timeout_type = init
    else:
        p, browser, context, page, init_error = init

    requests_headers = {}
    if user_agent:
        requests_headers["User-Agent"] = user_agent

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

        if page is not None:
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if response is not None:
                    status_code = str(response.status)
                title, description = _extract_head_metadata(page)
            except playwright_timeout_type:
                error = "Timeout"
            except playwright_error_type as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        else:
            try:
                response = requests.get(
                    url,
                    headers=requests_headers,
                    timeout=max(1, int(timeout_ms / 1000)),
                    allow_redirects=True,
                )
                status_code = str(response.status_code)
                title, description = _extract_metadata_from_html(response.text)
                if init_error:
                    error = f"Playwright unavailable; used HTTP fallback. {init_error}"
            except requests.Timeout:
                error = "Timeout"
            except Exception as exc:
                error = str(exc)

        yield CrawlResult(
            url=url,
            meta_title=title,
            meta_description=description,
            status=status_code,
            error=error,
        )

        if domain:
            last_hit_by_domain[domain] = time.monotonic()

    if context is not None:
        context.close()
    if browser is not None:
        browser.close()
    if p is not None:
        p.stop()
