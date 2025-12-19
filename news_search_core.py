"""Поиск новостей (DuckDuckGo / ddgs) с загрузкой полного текста — портировано из news_app.

Ключевая цель: отдавать максимально полный текст (без обрезания) + картинку (preview/article).
"""

from __future__ import annotations

import email.utils as email_utils
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
from urllib.parse import urljoin, urlparse, unquote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from bs4 import BeautifulSoup  # type: ignore[import]
except Exception:  # pragma: no cover
    BeautifulSoup = None


# Пул потоков для IO-bound задач
DEFAULT_WORKERS = int(os.environ.get("NEWS_FETCH_WORKERS", "12"))
executor = ThreadPoolExecutor(max_workers=DEFAULT_WORKERS)

# Reusable requests Session with connection-pooling and retries
session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
)
retries = Retry(total=3, connect=3, read=3, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504))
adapter = HTTPAdapter(max_retries=retries, pool_connections=DEFAULT_WORKERS, pool_maxsize=DEFAULT_WORKERS)
session.mount("http://", adapter)
session.mount("https://", adapter)


def _extract_text_from_html(html: str) -> str | None:
    """Best-effort HTML -> plain text extraction without native deps.

    This intentionally avoids lxml/trafilatura/readability to keep Android builds
    reliable (pure-Python stack only).
    """

    if not html:
        return None

    if BeautifulSoup is None:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Drop obvious boilerplate/noise
    for tag in soup.find_all(["script", "style", "noscript", "svg", "canvas"]):
        try:
            tag.decompose()
        except Exception:
            pass
    for tag in soup.find_all(["header", "footer", "nav", "aside", "form"]):
        try:
            tag.decompose()
        except Exception:
            pass

    node = soup.find("article") or soup.find("main") or soup.body or soup

    chunks: list[str] = []
    for el in node.find_all(["h1", "h2", "h3", "p", "li"], limit=400):
        t = el.get_text(" ", strip=True)
        if t and len(t) >= 40:
            chunks.append(t)

    if chunks:
        return "\n\n".join(chunks)

    # Fallback: raw text
    txt = node.get_text("\n", strip=True)
    return txt or None


def _parse_date(date_str: str):
    """Попытаться распарсить дату из строки в datetime (UTC). Возвращает None при неудаче."""
    if not date_str:
        return None
    try:
        s = date_str.strip()
        if s.endswith("Z"):
            s = s[:-1]
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        try:
            dt = email_utils.parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            return None


def _google_news_rss_search(query: str, max_results: int = 20) -> List[Dict[str, str]]:
    """Fallback news search via Google News RSS (pure-Python).

    Returns items in a DDG-like shape: title/url/date/source/body/image.
    """

    q = (query or "").strip()
    if not q:
        return []

    rss_url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(q)
        + "&hl=ru&gl=RU&ceid=RU:ru"
    )

    resp = session.get(rss_url, timeout=(5, 20))
    if not resp.ok:
        return []

    # Google RSS is XML; ElementTree is sufficient.
    try:
        root = ET.fromstring(resp.text)
    except Exception:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    out: List[Dict[str, str]] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()

        # Optional: <source> tag contains publisher name.
        src_tag = item.find("source")
        source = (src_tag.text or "").strip() if src_tag is not None else "Google News"

        if not link:
            continue
        body = ""
        image = None
        # Google News RSS often embeds snippet + preview image in <description> as HTML.
        if desc:
            if BeautifulSoup is not None:
                try:
                    soup = BeautifulSoup(desc, "html.parser")
                    img = soup.find("img")
                    if img and img.get("src"):
                        image = img.get("src")
                    body = soup.get_text(" ", strip=True) or ""
                except Exception:
                    body = re.sub(r"<[^>]+>", " ", desc)
            else:
                body = re.sub(r"<[^>]+>", " ", desc)

        # Normalize whitespace
        body = re.sub(r"\s+", " ", body).strip()

        out.append(
            {
                "title": title,
                "url": link,
                "date": pub,
                "source": source or "Google News",
                "body": body,
                "image": image,
            }
        )
        if len(out) >= max_results:
            break

    return out


def _extract_google_news_source_url(html: str, base_url: str) -> str | None:
    """Best-effort extraction of publisher URL from a Google News landing page."""
    if not html:
        return None

    # Prefer canonical/og:url first (often points to publisher)
    try:
        m = re.search(
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
            html,
            flags=re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
                html,
                flags=re.IGNORECASE,
            )
        if m:
            u = m.group(1)
            u = urljoin(base_url, u)
            if u:
                return u
    except Exception:
        pass

    # Google often includes a redirect like ...?url=https%3A%2F%2Fpublisher...
    try:
        for m in re.finditer(r"[?&]url=([^&\"'>]+)", html, flags=re.IGNORECASE):
            raw = m.group(1)
            cand = unquote(raw)
            if cand.startswith("http"):
                host = urlparse(cand).netloc.lower()
                if host and "google." not in host:
                    return cand
    except Exception:
        pass

    # BS4 fallback: pick first non-google https link in anchors
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a.get("href")
                if not href:
                    continue
                href = urljoin(base_url, href)
                if not href.startswith("http"):
                    continue
                host = urlparse(href).netloc.lower()
                if host and "google." not in host:
                    return href
        except Exception:
            pass

    return None


def _fetch_article_text(url: str, existing_image: str = None, _depth: int = 0) -> dict:
    """Скачивает и извлекает полный текст новости по ссылке + картинку (news_app логика)."""
    try:
        # Use shared session (connection pooling) and a split timeout (connect, read)
        response = session.get(url, timeout=(5, 20))
        # Не затираем корректную кодировку из заголовков; но если requests поставил latin-1,
        # пробуем определить реальную.
        if (not response.encoding) or (response.encoding.lower() in {"iso-8859-1", "latin-1"}):
            response.encoding = response.apparent_encoding or "utf-8"

        if not response.ok:
            return {"full_text": f"Ошибка загрузки: {response.status_code}", "image": None}

        # Важно: favor полноту, tables иногда содержат основной контент
        html = response.text

        # If we landed on a Google News page, try to jump to the publisher once.
        try:
            if _depth < 1 and ("news.google.com" in (response.url or "") or "news.google.com" in (url or "")):
                src = _extract_google_news_source_url(html, response.url or url)
                if src and src != url:
                    alt = _fetch_article_text(src, existing_image=existing_image, _depth=_depth + 1)
                    alt_text = (alt.get("full_text") or "").strip()
                    if len(alt_text) > 200:
                        return alt
        except Exception:
            pass

        # Pure-Python extraction (avoid lxml-based dependencies in Android builds)
        text = _extract_text_from_html(html)
        # Если это агрегатор/пересказ и текста мало — попробуем перейти на canonical/первоисточник (1 шаг)
        if (
            _depth < 1
            and (text is None or len((text or "").strip()) < 800)
            and "<html" in html.lower()
        ):
            try:
                source_url = None
                # Быстрый regex для canonical/og:url
                m = re.search(
                    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
                    html,
                    flags=re.IGNORECASE,
                )
                if not m:
                    m = re.search(
                        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
                        html,
                        flags=re.IGNORECASE,
                    )
                if m:
                    source_url = m.group(1)
                else:
                    # Пытаемся найти ссылку "Источник"/"Первоисточник" через bs4
                    if BeautifulSoup is not None:
                        soup = BeautifulSoup(html, "html.parser")
                        for a in soup.find_all("a", href=True):
                            label = a.get_text(" ", strip=True).lower()
                            if any(k in label for k in ("источник", "первоисточник", "original", "source")):
                                source_url = a["href"]
                                break

                if source_url and source_url != url:
                    # Некоторые сайты дают относительные URL
                    try:
                        source_url = urljoin(url, source_url)
                    except Exception:
                        pass
                    alt = _fetch_article_text(source_url, existing_image=existing_image, _depth=_depth + 1)
                    alt_text = (alt.get("full_text") or "").strip()
                    if len(alt_text) > len((text or "").strip()):
                        return alt
            except Exception:
                pass

        if text is None:
            formatted_text = "Не удалось извлечь текст (возможно, защита от ботов)."
        else:
            paragraphs = text.split("\n")
            clean_paragraphs = [p.strip() for p in paragraphs if p.strip()]
            formatted_text = "\n\n".join(clean_paragraphs)

        # Попытка извлечь изображение: existing_image, быстрый regex, затем BS4 как fallback
        image_url = None
        if existing_image:
            image_url = existing_image
        else:
            try:
                html = response.text
                m = re.search(
                    r"<meta[^>]+property=[\"\']og:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']",
                    html,
                    flags=re.IGNORECASE,
                )
                if not m:
                    m = re.search(
                        r"<meta[^>]+name=[\"\']twitter:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']",
                        html,
                        flags=re.IGNORECASE,
                    )
                if m:
                    image_url = m.group(1)
                else:
                    m2 = re.search(r"<img[^>]+src=[\"\']([^\"\']+)[\"\']", html, flags=re.IGNORECASE)
                    if m2:
                        image_url = m2.group(1)
            except Exception:
                pass

            if not image_url:
                if BeautifulSoup is not None:
                    try:
                        soup = BeautifulSoup(response.text, "html.parser")
                        og = soup.find("meta", property="og:image")
                        if og and og.get("content"):
                            image_url = og.get("content")
                        if not image_url:
                            tw = soup.find("meta", attrs={"name": "twitter:image"})
                            if tw and tw.get("content"):
                                image_url = tw.get("content")
                        if not image_url:
                            first_img = soup.find("img")
                            if first_img and first_img.get("src"):
                                image_url = first_img.get("src")
                        if image_url:
                            image_url = urljoin(response.url, image_url)
                    except Exception:
                        pass

        return {"full_text": formatted_text, "image": image_url}
    except Exception as e:
        return {"full_text": f"Ошибка при загрузке статьи: {e}", "image": None}


def get_news_with_content(query: str, max_results: int = 6) -> List[Dict]:
    """Новости из RSS.

    По умолчанию возвращаем быстрые результаты (title/link/snippet/image) без
    скачивания и парсинга полного текста — это ускоряет поиск в разы на телефоне.

    Полный текст нужно получать по требованию через fetch_article()/fetch_article_text().

    NOTE: For Android build reliability we avoid DDG client libraries (ddgs pulls
    in native lxml). Primary source is Google News RSS.
    """
    return get_news(query, max_results=max_results, fetch_full_text=False)


def get_news(query: str, max_results: int = 6, fetch_full_text: bool = False) -> List[Dict]:
    """Возвращает новости. Если fetch_full_text=True — параллельно вытаскивает полный текст."""
    if not query:
        return []

    search_query = query
    if len(query.split()) < 3 and "новости" not in query.lower():
        search_query = f"{query} новости"

    fetch_candidates = max(max_results, 30)

    # Primary: Google News RSS
    try:
        results = _google_news_rss_search(search_query, max_results=fetch_candidates)
    except Exception:
        return []

    # Фильтруем результаты по дате — расширяющееся окно (3, 7, 14 дней)
    now_utc = datetime.now(timezone.utc)
    cutoff_options = [3, 7, 14]
    parsed = [(item, _parse_date(item.get("date"))) for item in results]

    filtered_results: List[Dict] = []
    min_desired = min(max_results, 6)
    for days in cutoff_options:
        cutoff = now_utc - timedelta(days=days)
        filtered_results = [item for (item, dt) in parsed if dt is not None and dt >= cutoff]
        if len(filtered_results) >= min_desired:
            break

    # Если всё ещё мало — допускаем статьи без parseable date (в порядке появления)
    if len(filtered_results) < min_desired:
        no_date_items = [item for (item, dt) in parsed if dt is None]
        if no_date_items:
            needed = min_desired - len(filtered_results)
            filtered_results.extend(no_date_items[:needed])

    filtered_results = filtered_results[:max_results]

    # Fast path: do not fetch full text during search.
    if not fetch_full_text:
        news_list: List[Dict] = []
        for item in filtered_results:
            news_list.append(
                {
                    "title": item.get("title"),
                    "link": item.get("url"),
                    "published": item.get("date"),
                    "source": item.get("source"),
                    "description": item.get("body") or "",
                    "image": item.get("image"),
                    "full_text": "",
                }
            )
        return news_list

    # Slow path: fetch full text in parallel.
    futures = []
    for item in filtered_results:
        url = item.get("url")
        existing_image = item.get("image")
        futures.append(executor.submit(_fetch_article_text, url, existing_image))

    news_list2: List[Dict] = []
    for item, fut in zip(filtered_results, futures):
        try:
            res = fut.result()
        except Exception:
            res = {"full_text": "", "image": None}

        image_url = item.get("image") or res.get("image")
        news_item = {
            "title": item.get("title"),
            "link": item.get("url"),
            "published": item.get("date"),
            "source": item.get("source"),
            "description": item.get("body") or "",
            "image": image_url,
            "full_text": res.get("full_text") or "",
        }
        news_list2.append(news_item)

    return news_list2


def fetch_article(url: str) -> dict:
    """Единичная загрузка статьи (текст + картинка)."""
    return _fetch_article_text(url) or {"full_text": "", "image": None}


def fetch_article_text(url: str) -> str:
    """Единичная загрузка текста (без обрезания)."""
    return (fetch_article(url) or {}).get("full_text") or ""


__all__ = ["get_news_with_content", "get_news", "fetch_article", "fetch_article_text"]
