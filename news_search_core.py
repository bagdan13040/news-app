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
from duckduckgo_search import DDGS
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
                        from urllib.parse import urljoin

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
                            from urllib.parse import urljoin

                            image_url = urljoin(response.url, image_url)
                    except Exception:
                        pass

        return {"full_text": formatted_text, "image": image_url}
    except Exception as e:
        return {"full_text": f"Ошибка при загрузке статьи: {e}", "image": None}


def get_news_with_content(query: str, max_results: int = 6) -> List[Dict]:
    """Синхронная версия: новости через DuckDuckGo + полный текст параллельно (news_app подход)."""
    if not query:
        return []

    search_query = query
    if len(query.split()) < 3 and "новости" not in query.lower():
        search_query = f"{query} новости"

    try:
        fetch_candidates = max(max_results, 20)
        results = list(
            DDGS().news(
                search_query,
                region="ru-ru",
                safesearch="off",
                timedomain="d",
                max_results=fetch_candidates,
            )
        )
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

    # Параллельно качаем статьи
    futures = []
    for item in filtered_results:
        url = item.get("url")
        existing_image = item.get("image")
        futures.append(executor.submit(_fetch_article_text, url, existing_image))

    news_list: List[Dict] = []
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
            "description": item.get("body"),
            "image": image_url,
            "full_text": res.get("full_text") or "",
        }
        news_list.append(news_item)

    return news_list


def fetch_article_text(url: str) -> str:
    """Единичная загрузка текста (без обрезания)."""
    return (_fetch_article_text(url) or {}).get("full_text") or ""


__all__ = ["get_news_with_content", "fetch_article_text"]
