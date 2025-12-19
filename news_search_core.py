"""
News search and parsing core.
Rewritten from scratch to be simple and robust.
Based on mobile_pack implementation using trafilatura.
"""
from ddgs import DDGS
from typing import List, Dict
from datetime import datetime, timedelta, timezone
import email.utils as email_utils
import trafilatura
import requests
import os
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor

import xml.etree.ElementTree as ET
from urllib.parse import quote

# Thread pool for parallel fetching
DEFAULT_WORKERS = int(os.environ.get("NEWS_FETCH_WORKERS", "12"))
executor = ThreadPoolExecutor(max_workers=DEFAULT_WORKERS)

# Reusable requests Session with connection-pooling and retries
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
})
retries = Retry(total=2, backoff_factor=0.3, status_forcelist=(500, 502, 503, 504))
adapter = HTTPAdapter(max_retries=retries, pool_connections=DEFAULT_WORKERS, pool_maxsize=DEFAULT_WORKERS)
session.mount('http://', adapter)
session.mount('https://', adapter)

# Check for trafilatura availability
TRAFILATURA_AVAILABLE = True

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

def _parse_date(date_str: str):
    if not date_str:
        return None
    try:
        s = date_str.strip()
        if s.endswith('Z'):
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

def search_google_news_rss(query: str, max_results: int = 20) -> List[Dict]:
    """Search Google News RSS feed."""
    try:
        encoded_query = quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ru&gl=RU&ceid=RU:ru"
        response = session.get(url, timeout=10)
        if not response.ok:
            return []
        
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall('.//item'):
            title = item.find('title').text if item.find('title') is not None else "No title"
            link = item.find('link').text if item.find('link') is not None else ""
            pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
            description = item.find('description').text if item.find('description') is not None else ""
            source = item.find('source').text if item.find('source') is not None else "Google News"
            
            # Clean description (often contains HTML)
            if description and BeautifulSoup:
                try:
                    description = BeautifulSoup(description, 'html.parser').get_text()
                except:
                    pass

            items.append({
                "title": title,
                "link": link,
                "published": pub_date,
                "source": source,
                "description": description,
                "image": None, # RSS doesn't usually have images easily
                "full_text": ""
            })
            if len(items) >= max_results:
                break
        return items
    except Exception as e:
        print(f"Google News RSS error: {e}")
        return []

def _fetch_article_text(url: str, existing_image: str = None) -> dict:
    """
    Download and extract full text from a URL.
    Uses trafilatura as primary method, with simple BeautifulSoup fallback.
    """
    try:
        response = session.get(url, timeout=(5, 10))
        # Fix encoding manually if needed, but requests usually does ok
        response.encoding = response.apparent_encoding
        
        if not response.ok:
            return {"full_text": f"Error: {response.status_code}", "image": None}

        html = response.text
        
        # 1. Try Trafilatura (Primary)
        text = None
        try:
            # This is the key line from mobile_pack
            text = trafilatura.extract(html, target_language='ru')
        except Exception as e:
            print(f"Trafilatura error: {e}")

        # 2. Fallback to BeautifulSoup if Trafilatura failed or returned empty
        if not text and BeautifulSoup:
            try:
                soup = BeautifulSoup(html, 'html.parser')
                # Remove junk
                for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
                    tag.decompose()
                
                # Get text from paragraphs - simple and effective fallback
                paragraphs = []
                # Look for article body first
                article_body = soup.find('article') or soup.find('main') or soup.find('div', class_=re.compile('content|article|post')) or soup.body
                
                if article_body:
                    for p in article_body.find_all(['p', 'h2', 'h3', 'div']):
                        t = p.get_text(strip=True)
                        if len(t) > 10: # Lower threshold to capture almost everything
                            paragraphs.append(t)
                
                if paragraphs:
                    text = '\n\n'.join(paragraphs)
            except Exception as e:
                print(f"BeautifulSoup error: {e}")

        if not text:
            # Last resort: just dump all text from body
            if BeautifulSoup:
                try:
                    text = soup.body.get_text("\n\n", strip=True)
                except:
                    pass
            
            if not text:
                formatted_text = "Не удалось извлечь текст."
            else:
                formatted_text = text
        else:
            # Clean up text
            paragraphs = text.split('\n')
            clean_paragraphs = [p.strip() for p in paragraphs if p.strip()]
            formatted_text = '\n\n'.join(clean_paragraphs)

        # Image extraction
        image_url = None
        if existing_image:
            image_url = existing_image
        else:
            # Regex for og:image
            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
            if m:
                image_url = m.group(1)
            
            # Fallback to first img tag
            if not image_url:
                m2 = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
                if m2:
                    image_url = m2.group(1)
            
            # Resolve relative URLs
            if image_url and not image_url.startswith('http'):
                from urllib.parse import urljoin
                image_url = urljoin(url, image_url)

        return {"full_text": formatted_text, "image": image_url}

    except Exception as e:
        return {"full_text": f"Error: {e}", "image": None}

def get_news_with_content(query: str, max_results: int = 5) -> List[Dict]:
    """
    Search news and fetch content.
    Combines Google News RSS (primary) and DuckDuckGo (secondary).
    """
    if not query:
        return []

    print(f"Searching for: '{query}'...")
    
    all_results = []
    seen_links = set()

    # 1. Google News RSS (Primary)
    try:
        gn_results = search_google_news_rss(query, max_results=max_results)
        for item in gn_results:
            if item['link'] not in seen_links:
                all_results.append(item)
                seen_links.add(item['link'])
    except Exception as e:
        print(f"Google News search error: {e}")

    # 2. DuckDuckGo (Secondary - if needed)
    if len(all_results) < max_results:
        search_query = query
        if len(query.split()) < 3 and "новости" not in query.lower():
            search_query = f"{query} новости"

        try:
            ddg_results = list(DDGS().news(
                search_query,
                region="ru-ru",
                safesearch="off",
                timedomain="d",
                max_results=max(max_results, 20),
            ))
            
            for item in ddg_results:
                link = item.get("url")
                if link and link not in seen_links:
                    all_results.append({
                        "title": item.get("title"),
                        "link": link,
                        "published": item.get("date"),
                        "source": item.get("source"),
                        "description": item.get("body"),
                        "image": item.get("image"),
                        "full_text": ""
                    })
                    seen_links.add(link)
        except Exception as e:
            print(f"DDG Search error: {e}")
    
    # Filter by date (last 3 days)
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=3)
    filtered_results = []
    
    for item in all_results:
        dt = _parse_date(item.get('published'))
        if dt and dt >= cutoff:
            filtered_results.append(item)
            
    # If not enough recent news, take whatever we have
    if len(filtered_results) < max_results:
        remaining = [x for x in all_results if x not in filtered_results]
        filtered_results.extend(remaining)
        
    return filtered_results[:max_results]

def fetch_article(url: str) -> dict:
    """Fetch single article content."""
    return _fetch_article_text(url)

def fetch_article_text(url: str) -> str:
    """Fetch just the text."""
    res = _fetch_article_text(url)
    return res.get("full_text", "")

if __name__ == "__main__":
    # Test
    res = get_news_with_content("Nvidia", max_results=1)
    if res:
        print(f"Title: {res[0]['title']}")
        print("Fetching text...")
        art = fetch_article(res[0]['link'])
        print(f"Text len: {len(art['full_text'])}")
        print(f"Text start: {art['full_text'][:100]}")
