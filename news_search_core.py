"""Поиск новостей через DuckDuckGo с загрузкой полного текста.

Ключевая цель: отдавать максимально полный текст + картинку.
"""

from __future__ import annotations

import email.utils as email_utils
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    DDGS = None

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
    
    Improved algorithm to extract FULL content (complete articles).
    """

    if not html:
        return None

    if BeautifulSoup is None:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Drop obvious boilerplate/noise
    for tag in soup.find_all(["script", "style", "noscript", "svg", "canvas", "iframe"]):
        try:
            tag.decompose()
        except Exception:
            pass
    
    # Remove common non-content elements
    for tag in soup.find_all(["header", "footer", "nav", "aside", "form"]):
        try:
            tag.decompose()
        except Exception:
            pass
    
    # Remove elements with common ad/promo classes
    for selector in [".ad", ".advertisement", ".promo", ".related", ".sidebar", ".comments", ".share", ".social"]:
        for tag in soup.select(selector):
            try:
                tag.decompose()
            except Exception:
                pass

    # Find main content container - try multiple strategies
    node = None
    
    # Strategy 1: article tag
    node = soup.find("article")
    
    # Strategy 2: main tag
    if not node:
        node = soup.find("main")
    
    # Strategy 3: div with content-related classes
    if not node:
        for cls in ["article", "content", "post", "entry", "story", "text"]:
            node = soup.find("div", class_=re.compile(cls, re.IGNORECASE))
            if node:
                break
    
    # Strategy 4: fallback to body
    if not node:
        node = soup.body or soup

    # Extract ALL text content from relevant tags
    chunks: list[str] = []
    
    # Get headers and paragraphs in order - keep structure
    for el in node.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "code"]):
        t = el.get_text(" ", strip=True)
        # Lower minimum length to capture more content
        if t and len(t) >= 10:
            chunks.append(t)
    
    if chunks:
        # Join with double newlines for paragraph separation
        text = "\n\n".join(chunks)
        # Clean up excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

    # Fallback: get all text from node
    txt = node.get_text("\n", strip=True)
    if txt:
        # Clean up excessive whitespace in fallback too
        txt = re.sub(r'\n{3,}', '\n\n', txt)
        txt = re.sub(r' {2,}', ' ', txt)
        return txt.strip()
    
    return None


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


def _search_news_ddg(query: str, max_results: int = 20) -> List[Dict[str, str]]:
    """Search news via DuckDuckGo.
    
    Returns items with: title, url, date, source, body, image.
    """
    if not DDGS_AVAILABLE:
        print("DuckDuckGo library not available, returning empty results")
        return []
    
    q = (query or "").strip()
    if not q:
        return []
    
    # Add "новости" to short queries for better relevance
    search_query = q
    if len(q.split()) < 3 and "новости" not in q.lower():
        search_query = f"{q} новости"
    
    try:
        results = list(DDGS().news(
            search_query,
            region="ru-ru",
            safesearch="off",
            timedomain="d",
            max_results=max_results,
        ))
    except Exception as e:
        print(f"DDG search error: {e}")
        return []
    
    out: List[Dict[str, str]] = []
    for item in results:
        out.append({
            "title": item.get("title", "").strip(),
            "url": item.get("url", "").strip(),
            "date": item.get("date", "").strip(),
            "source": item.get("source", "").strip(),
            "body": item.get("body", "").strip(),
            "image": item.get("image"),
        })
    
    return out


def _fetch_article_text(url: str, existing_image: str = None, _depth: int = 0) -> dict:
    """Скачивает и извлекает полный текст новости по ссылке + картинку."""
    try:
        # Use shared session (connection pooling) and a split timeout (connect, read)
        response = session.get(url, timeout=(5, 20), allow_redirects=True)
        # Не затираем корректную кодировку из заголовков
        if (not response.encoding) or (response.encoding.lower() in {"iso-8859-1", "latin-1"}):
            response.encoding = response.apparent_encoding or "utf-8"

        if not response.ok:
            return {"full_text": f"Ошибка загрузки: {response.status_code}", "image": None}

        html = response.text

        # Pure-Python extraction
        text = _extract_text_from_html(html)
        
        # Если текста мало и это агрегатор — попробуем перейти на canonical/первоисточник
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

            if not image_url and BeautifulSoup is not None:
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
    """Новости из DuckDuckGo.

    По умолчанию возвращаем быстрые результаты (title/link/snippet/image) без
    скачивания и парсинга полного текста — это ускоряет поиск в разы на телефоне.

    Полный текст нужно получать по требованию через fetch_article()/fetch_article_text().
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

    # Primary: DuckDuckGo
    try:
        results = _search_news_ddg(search_query, max_results=fetch_candidates)
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
