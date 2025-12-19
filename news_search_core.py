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

# Thread pool for parallel fetching
DEFAULT_WORKERS = int(os.environ.get("NEWS_FETCH_WORKERS", "12"))
executor = ThreadPoolExecutor(max_workers=DEFAULT_WORKERS)

# Reusable requests Session with connection-pooling and retries
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
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
    Fast mode: returns results immediately, full text is fetched on demand via fetch_article.
    """
    if not query:
        return []

    print(f"Searching for: '{query}'...")
    
    search_query = query
    if len(query.split()) < 3 and "новости" not in query.lower():
        search_query = f"{query} новости"

    try:
        # Use ddgs to search
        results = list(DDGS().news(
            search_query,
            region="ru-ru",
            safesearch="off",
            timedomain="d",
            max_results=max(max_results, 20),
        ))
    except Exception as e:
        print(f"Search error: {e}")
        return []
    
    # Filter by date (last 3 days)
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=3)
    filtered_results = []
    
    for item in results:
        dt = _parse_date(item.get('date'))
        if dt and dt >= cutoff:
            filtered_results.append(item)
            
    # If not enough recent news, take whatever we have
    if len(filtered_results) < max_results:
        remaining = [x for x in results if x not in filtered_results]
        filtered_results.extend(remaining)
        
    filtered_results = filtered_results[:max_results]

    news_list = []
    for item in filtered_results:
        news_list.append({
            "title": item.get("title"),
            "link": item.get("url"),
            "published": item.get("date"),
            "source": item.get("source"),
            "description": item.get("body"),
            "image": item.get("image"),
            "full_text": "" # Loaded on demand
        })
        
    return news_list

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
