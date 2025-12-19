"""Backend для получения данных погоды, финансов и трендов."""
import time
import requests
import xml.etree.ElementTree as ET
from threading import Lock
from typing import Dict, List

# Простой кэш с TTL
_cache: Dict[str, tuple] = {}
_cache_lock = Lock()


def _get_from_cache(key: str, ttl: int):
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        ts, val = entry
        if time.time() - ts < ttl:
            return val
        del _cache[key]
        return None


def _set_cache(key: str, val):
    with _cache_lock:
        _cache[key] = (time.time(), val)


def fetch_json(url: str, timeout: tuple = (3, 8)) -> dict:
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.ok:
            return resp.json()
        return {}
    except Exception:
        return {}


def get_weather(lat: float = 54.74, lon: float = 55.97, ttl: int = 300) -> Dict:
    """Получить текущую погоду из open-meteo (без API ключа)."""
    key = f"weather::{lat}:{lon}"
    cached = _get_from_cache(key, ttl)
    if cached:
        return cached

    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto"
    data = fetch_json(url)
    out = {}
    cur = data.get('current_weather') or {}
    out['temperature'] = cur.get('temperature')
    out['windspeed'] = cur.get('windspeed')
    out['weathercode'] = cur.get('weathercode')
    out['winddirection'] = cur.get('winddirection')
    out['time'] = cur.get('time')
    out['raw'] = data
    _set_cache(key, out)
    return out


def get_yahoo_price(symbol: str, ttl: int = 300) -> float:
    """Получить цену с Yahoo Finance."""
    key = f"yahoo::{symbol}"
    cached = _get_from_cache(key, ttl)
    if cached:
        return cached

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.ok:
            data = resp.json()
            result = data.get('chart', {}).get('result', [])
            if result:
                price = result[0].get('meta', {}).get('regularMarketPrice')
                if price is not None:
                    _set_cache(key, price)
                    return price
    except Exception as e:
        print(f"Ошибка получения {symbol}: {e}")
    return 0.0


def get_financial_data(symbols: List[str] = None) -> Dict[str, float]:
    """Получить цены нескольких символов."""
    if symbols is None:
        symbols = [
            "RUB=X",      # USD/RUB
            "EURRUB=X",   # EUR/RUB
            "NVDA",       # NVIDIA
            "AAPL",       # Apple
            "GOOGL",      # Google
            "MSFT",       # Microsoft
            "TSLA",       # Tesla
            "AMZN",       # Amazon
            "BTC-USD",    # Bitcoin
            "ETH-USD",    # Ethereum
        ]
    results = {}
    for sym in symbols:
        results[sym] = get_yahoo_price(sym)
    return results


def get_google_trends(ttl: int = 1800) -> List[Dict[str, str]]:
    """Получить тренды из Google News RSS."""
    key = "google_trends_rss"
    cached = _get_from_cache(key, ttl)
    if cached:
        return cached

    url = "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru"
    try:
        # Используем сконфигурированный session из news_search_core
        from news_search_core import session
        
        print(f"[TRENDS] Fetching Google News trends from {url}")
        resp = session.get(url, timeout=(5, 10))
        print(f"[TRENDS] Response status: {resp.status_code}")
        
        if resp.ok:
            root = ET.fromstring(resp.content)
            items = root.findall('.//item')
            trends = []
            for item in items[:30]:  # Get top 30
                title_elem = item.find('title')
                if title_elem is not None and title_elem.text:
                    title = title_elem.text
                    if ' - ' in title:
                        title = title.rsplit(' - ', 1)[0]
                    trends.append({
                        "tag": "#News",
                        "name": title[:60],  # Обрежем длинные заголовки
                        "change": "Hot"
                    })
            if trends:
                print(f"[TRENDS] Found {len(trends)} trends")
                _set_cache(key, trends)
                return trends
            else:
                print("[TRENDS] No trends found in RSS")
    except Exception as e:
        print(f"[TRENDS] Ошибка получения трендов: {e}")
        import traceback
        traceback.print_exc()
    
    # Fallback
    print("[TRENDS] Using fallback trends")
    return [
        {"tag": "#AI", "name": "ИИ и Нейросети", "change": "+24%"},
        {"tag": "#Space", "name": "Космос", "change": "+12%"},
        {"tag": "#Crypto", "name": "Биткоин", "change": "+8%"},
    ]
