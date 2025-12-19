"""Поиск новостей (DuckDuckGo / ddgs) с загрузкой полного текста — портировано из news_app.

Ключевая цель: отдавать максимально полный текст (без обрезания) + картинку (preview/article).
"""

from __future__ import annotations

import base64
import email.utils as email_utils
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, unquote, urlparse, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from bs4 import BeautifulSoup  # type: ignore[import]
except Exception:  # pragma: no cover
    BeautifulSoup = None


# Пул потоков для IO-bound задач
DEFAULT_WORKERS = int(os.environ.get("NEWS_FETCH_WORKERS", "24"))
executor = ThreadPoolExecutor(max_workers=DEFAULT_WORKERS)

# Reusable requests Session with connection-pooling and retries
session = requests.Session()

# На Android используем certifi для SSL сертификатов
try:
    import certifi
    import ssl
    # Проверяем есть ли certifi сертификаты
    if os.path.exists(certifi.where()):
        session.verify = certifi.where()
        print(f"[SSL] Using certifi bundle: {certifi.where()}")
except ImportError:
    print("[SSL] certifi not available, using system certificates")
except Exception as e:
    print(f"[SSL] Error configuring SSL: {e}")

session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
)
# Try to bypass Google Consent
session.cookies.set("CONSENT", "YES+cb.20210328-17-p0.en+FX+417")
session.cookies.set("SOCS", "CAESV3Zn") # Additional consent cookie

retries = Retry(total=3, connect=3, read=3, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504))
adapter = HTTPAdapter(max_retries=retries, pool_connections=DEFAULT_WORKERS, pool_maxsize=DEFAULT_WORKERS)
session.mount("http://", adapter)
session.mount("https://", adapter)


def _parse_protobuf(data):
    """
    Simple protobuf parser to find the first URL string.
    """
    idx = 0
    length = len(data)
    
    while idx < length:
        if idx + 1 > length: break
        
        # Read tag (varint)
        shift = 0
        result = 0
        while True:
            if idx >= length: break
            byte = data[idx]
            idx += 1
            result |= (byte & 0x7f) << shift
            if not (byte & 0x80):
                break
            shift += 7
            
        wire_type = result & 7
        
        if wire_type == 0: # Varint
            while idx < length and (data[idx] & 0x80):
                idx += 1
            idx += 1
        elif wire_type == 1: # 64-bit
            idx += 8
        elif wire_type == 2: # Length-delimited (String, Bytes, Message)
            if idx >= length: break
            
            # Read length (varint)
            shift = 0
            size = 0
            while True:
                if idx >= length: break
                byte = data[idx]
                idx += 1
                size |= (byte & 0x7f) << shift
                if not (byte & 0x80):
                    break
                shift += 7
            
            data_start = idx
            idx += size
            
            if idx > length: break
            
            field_data = data[data_start:idx]
            
            # Check if it's a URL
            try:
                s = field_data.decode('utf-8')
                if s.startswith('http://') or s.startswith('https://'):
                    return s
            except:
                pass
            
            # Recursively check if it's a nested message
            found = _parse_protobuf(field_data)
            if found:
                return found

        elif wire_type == 5: # 32-bit
            idx += 4
        else:
            # Unknown wire type, stop
            break
            
    return None


def decode_google_news_url(url: str) -> str:
    """
    Decodes the Google News RSS URL to get the original source URL.
    Attempts to decode the protobuf first (offline).
    """
    # Check if it's a Google News URL
    if "news.google.com/rss/articles/" not in url and "news.google.com/articles/" not in url:
        return url

    base64_str = None
    try:
        # Extract the base64 part
        match = re.search(r'/articles/([a-zA-Z0-9\-_]+)', url)
        if match:
            base64_str = match.group(1)
            
            # Fix padding
            pad = len(base64_str) % 4
            if pad:
                base64_str += "=" * (4 - pad)
                
            decoded_bytes = base64.urlsafe_b64decode(base64_str)
            
            # Try to find a URL string in the protobuf
            decoded_url = _parse_protobuf(decoded_bytes)
            if decoded_url:
                return decoded_url
    except Exception:
        pass

    # Если не удалось декодировать, пробуем просто перейти (Google иногда редиректит)
    return url


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

    print(f"[RSS] Fetching Google News RSS: {rss_url}")
    try:
        resp = session.get(rss_url, timeout=(10, 30))
        print(f"[RSS] Response status: {resp.status_code}")
        if not resp.ok:
            print(f"[RSS] Bad response: {resp.status_code}")
            return []
    except Exception as e:
        print(f"[RSS] Request failed: {type(e).__name__}: {e}")
        raise

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

        # Optional: <source> tag contains publisher name.
        src_tag = item.find("source")
        source = (src_tag.text or "").strip() if src_tag is not None else "Google News"
        
        # Try to get description
        description = (item.findtext("description") or "").strip()
        # Clean up HTML from description if present (Google News RSS often has HTML)
        if description and "<" in description:
             # Simple tag stripping
             description = re.sub(r'<[^>]+>', '', description)

        if not link:
            continue
        out.append(
            {
                "title": title,
                "url": link,
                "date": pub,
                "source": source or "Google News",
                "body": description,
                "image": None,
            }
        )
        if len(out) >= max_results:
            break

    return out


def _search_ddg_fallback(title: str) -> str | None:
    """Поиск прямой ссылки через Bing RSS и DuckDuckGo HTML (fallback)."""
    if not title:
        return None
    
    clean_title = re.sub(r'[^\w\s\-\.,]', ' ', title).strip()
    first_words = ' '.join(clean_title.split()[:10])  # Первые 10 слов для поиска
    
    # Try Bing RSS first
    try:
        url = f"https://www.bing.com/news/search?q={quote_plus(first_words)}&format=rss"
        resp = session.get(url, timeout=8)
        
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            # Ищем первые 3 результата
            for item in root.findall(".//item")[:3]:
                link = item.findtext("link")
                if link:
                    # Extract url param if present (Bing wraps links)
                    parsed = urlparse(link)
                    qs = parse_qs(parsed.query)
                    if "url" in qs:
                        link = qs["url"][0]
                    # Проверяем что это не сам Google News
                    if "news.google.com" not in link and "consent.google.com" not in link:
                        return link
    except Exception as e:
        pass
    
    # Try DuckDuckGo HTML parsing as second fallback
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(first_words + ' news')}"
        resp = session.get(url, timeout=8)
        
        if resp.status_code == 200 and BeautifulSoup:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Ищем ссылки результатов
            for result in soup.find_all("a", class_="result__a")[:3]:
                link = result.get("href")
                if link and link.startswith("http") and "news.google.com" not in link and "consent.google.com" not in link:
                    # DDG может оборачивать ссылки
                    if "uddg=" in link:
                        parsed = urlparse(link)
                        qs = parse_qs(parsed.query)
                        if "uddg" in qs:
                            link = unquote(qs["uddg"][0])
                    return link
    except Exception as e:
        pass

    return None


def _fetch_article_text(url: str, existing_image: str = None, _depth: int = 0, title: str = None) -> dict:
    """Скачивает и извлекает полный текст новости по ссылке + картинку (news_app логика)."""
    
    # Try to decode Google News URL to avoid consent wall
    real_url = decode_google_news_url(url)
    if real_url != url:
        url = real_url

    try:
        # Use shared session (connection pooling) and a split timeout (connect, read)
        response = session.get(url, timeout=(5, 20))
        # Не затираем корректную кодировку из заголовков; но если requests поставил latin-1,
        # пробуем определить реальную.
        if (not response.encoding) or (response.encoding.lower() in {"iso-8859-1", "latin-1"}):
            response.encoding = response.apparent_encoding or "utf-8"

        if not response.ok:
            return {"full_text": f"Ошибка загрузки: {response.status_code}", "image": None}

        # Check if we are stuck on Google Consent page - расширенная проверка
        is_consent_page = (
            "consent.google.com" in response.url or 
            "consent.google" in response.url or
            ("before you continue" in response.text.lower() and "google" in response.text.lower()) or
            ("прежде чем продолжить" in response.text.lower() and "google" in response.text.lower()) or
            ("cookies" in response.text.lower() and "accept" in response.text.lower() and len(response.text) < 50000)
        )
        
        if is_consent_page:
             # Try fallback if title is available
             if title and _depth == 0:
                 alt_url = _search_ddg_fallback(title)
                 if alt_url:
                     # Check if fallback is also a google consent link (unlikely but possible)
                     if "consent.google.com" not in alt_url and "news.google.com" not in alt_url:
                        return _fetch_article_text(alt_url, existing_image, _depth=_depth + 1, title=None)
             
             return {"full_text": "Статья недоступна (требуется согласие на cookies). Попробуйте другую новость.", "image": None}

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


def get_news_with_content(query: str, max_results: int = 6, fetch_content: bool = True) -> List[Dict]:
    """Синхронная версия: новости + полный текст параллельно.

    NOTE: For Android build reliability we avoid DDG client libraries (ddgs pulls
    in native lxml). Primary source is Google News RSS.
    """
    if not query:
        return []

    search_query = query
    if len(query.split()) < 3 and "новости" not in query.lower():
        search_query = f"{query} новости"

    fetch_candidates = max(max_results * 3, 50)

    # Primary: Google News RSS
    try:
        results = _google_news_rss_search(search_query, max_results=fetch_candidates)
    except Exception:
        return []

    # Фильтруем результаты по дате — расширяющееся окно (7, 14, 30 дней)
    now_utc = datetime.now(timezone.utc)
    cutoff_options = [7, 14, 30]
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

    if not fetch_content:
        news_list: List[Dict] = []
        for item in filtered_results:
            news_item = {
                "title": item.get("title"),
                "link": item.get("url"),
                "published": item.get("date"),
                "source": item.get("source"),
                "description": item.get("body"),
                "image": item.get("image"),
                "full_text": "",
            }
            news_list.append(news_item)
        return news_list

    # Параллельно качаем статьи
    futures = []
    for item in filtered_results:
        url = item.get("url")
        existing_image = item.get("image")
        title = item.get("title")
        futures.append(executor.submit(_fetch_article_text, url, existing_image, 0, title))

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


def fetch_article_text(url: str, title: str = None) -> str:
    """Единичная загрузка текста (без обрезания)."""
    return (_fetch_article_text(url, title=title) or {}).get("full_text") or ""


def fetch_article_content(url: str, title: str = None) -> Dict[str, str]:
    """Скачивает текст и картинку."""
    res = _fetch_article_text(url, title=title)
    return {
        "full_text": res.get("full_text") or "",
        "image": res.get("image")
    }


__all__ = ["get_news_with_content", "fetch_article_text", "fetch_article_content"]
