import os
import time
import threading
import requests
from typing import List
from concurrent.futures import ThreadPoolExecutor

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    OpenAI = None

# Попытка загрузить .env (если есть)
try:
    from pathlib import Path
    from dotenv import load_dotenv
    _project_root = Path(__file__).resolve().parent
    load_dotenv(dotenv_path=_project_root / ".env", override=False)
except Exception:
    pass


class FastLLMClient:
    """Легковесный клиент для LLM операций с факт-чекингом."""

    def __init__(self):
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        self._base_url = base_url
        self._is_openrouter = "openrouter.ai" in str(base_url)
        self._api_key = (api_key or "").strip() or None
        
        # If the official SDK is installed we'll use it, otherwise fall back to a
        # minimal HTTP client (OpenAI/OpenRouter-compatible).
        if not self._api_key:
            print("Warning: LLM API key not found. Fact-checking will be unavailable.")
            self.client = None
        elif OPENAI_AVAILABLE:
            default_headers = None
            if self._is_openrouter:
                default_headers = {
                    "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost"),
                    "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "News Scout"),
                }
            self.client = OpenAI(base_url=base_url, api_key=api_key, default_headers=default_headers)
        else:
            # SDK not available -> we'll use HTTP mode.
            self.client = None

        models = os.environ.get("LLM_MODELS")
        if models:
            self.models = [m.strip() for m in models.split(",") if m.strip()]
        else:
            self.models = [
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
            ]

        self._executor = ThreadPoolExecutor(max_workers=4)
        self._cache = {}
        self._cache_ttl = int(os.environ.get("LLM_KEYWORDS_CACHE_TTL_S", 60 * 60 * 6))

    def configure(self, api_key: str, base_url: str = None):
        """Настройка API ключа в рантайме."""
        api_key = (api_key or "").strip()
        if not api_key:
            self.client = None
            self._api_key = None
            return
        if base_url:
            self._base_url = base_url.strip()
        self._is_openrouter = "openrouter.ai" in str(self._base_url)
        os.environ["OPENROUTER_API_KEY"] = api_key
        self._api_key = api_key
        
        if OPENAI_AVAILABLE:
            default_headers = None
            if self._is_openrouter:
                default_headers = {
                    "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost"),
                    "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "News Scout"),
                }
            self.client = OpenAI(base_url=self._base_url, api_key=api_key, default_headers=default_headers)
        else:
            self.client = None


    def _call_model_http(self, model: str, prompt: str, max_tokens: int, temperature: float) -> str:
        if not self._api_key:
            raise RuntimeError("No LLM client configured (missing API key)")

        base = (self._base_url or "").rstrip("/")
        url = f"{base}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._is_openrouter:
            headers.update(
                {
                    "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost"),
                    "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "News Scout"),
                }
            )

        payload = {
            "model": self._normalize_model(model),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=(5, 30))
        if not resp.ok:
            # Try to return a useful error message
            try:
                data = resp.json()
                err = data.get("error") or {}
                msg = err.get("message") or resp.text
            except Exception:
                msg = resp.text
            raise RuntimeError(f"LLM HTTP error {resp.status_code}: {str(msg)[:300]}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM HTTP error: empty choices")
        message = (choices[0].get("message") or {}).get("content")
        if not message:
            raise RuntimeError("LLM HTTP error: empty message")
        return str(message).strip()

    def _is_cache_valid(self, key: str) -> bool:
        entry = self._cache.get(key)
        if not entry:
            return False
        ts, _ = entry
        return (time.time() - ts) < self._cache_ttl

    def _cache_get(self, key: str):
        return self._cache.get(key, (None, None))[1]

    def _cache_set(self, key: str, value: List[str]):
        self._cache[key] = (time.time(), value)

    def _normalize_model(self, model: str) -> str:
        m = (model or "").strip()
        if self._is_openrouter and m and "/" not in m:
            return f"openai/{m}"
        return m

    def _call_model(self, model: str, prompt: str, max_tokens: int, temperature: float):
        if self.client is not None:
            model = self._normalize_model(model)
            resp = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()

        return self._call_model_http(model, prompt, max_tokens=max_tokens, temperature=temperature)

    def generate_related_keywords(self, query: str, max_keywords: int = 6, timeout: float = 3.0) -> List[str]:
        """Подбор связанных ключевых слов и синонимов для запроса.
        
        Использует LLM с кешем, fallback на DuckDuckGo Autocomplete и простые варианты.
        """
        if not query:
            return []
        cache_key = f"kw::{query.strip().lower()}::{max_keywords}"
        if self._is_cache_valid(cache_key):
            return self._cache_get(cache_key)

        # Если нет API ключа, используем детерминированные fallback
        if not self.client:
            fallback = [query.strip()]
            words = [w for w in query.split() if len(w) > 2]
            if len(words) >= 2:
                fallback.append(" ".join(words[:2]))
            if len(words) >= 3:
                fallback.append(" ".join(words[:3]))
            fallback.append(query + " новости")
            out = []
            seen = set()
            for p in fallback:
                low = p.lower()
                if low not in seen:
                    seen.add(low)
                    out.append(p)
                    if len(out) >= max_keywords:
                        break
            self._cache_set(cache_key, out)
            return out

        # Компактный промпт для экономии токенов
        prompt = f"Подбери до {max_keywords} коротких ключевых фраз на русском для поиска по теме: {query}. Ответ — одна строка через запятую."

        # Пробуем модели по порядку с таймаутом
        for model in self.models:
            try:
                future = self._executor.submit(self._call_model, model, prompt, max_tokens=80, temperature=0.12)
                text = future.result(timeout=timeout)
                if not text:
                    continue
                # Парсим результат в список
                parts = [p.strip() for p in text.replace('\n', ',').split(',') if p.strip()]
                out = []
                seen = set()
                for p in parts:
                    low = p.lower()
                    if low not in seen:
                        seen.add(low)
                        out.append(p)
                        if len(out) >= max_keywords:
                            break
                self._cache_set(cache_key, out)
                return out
            except Exception as e:
                print(f"LLM model {model} failed for keywords: {e}")

        # Fallback: DuckDuckGo Autocomplete (без API ключа, качественные синонимы)
        try:
            url = "https://duckduckgo.com/ac/"
            params = {'q': query, 'type': 'list'}
            resp = requests.get(url, params=params, timeout=2.0)
            if resp.ok:
                data = resp.json()
                # Формат: [query, [suggestion1, suggestion2, ...]]
                if len(data) >= 2 and isinstance(data[1], list):
                    suggestions = data[1]
                    out = []
                    seen = set()
                    for phrase in suggestions:
                        if phrase and phrase.lower() != query.lower() and phrase.lower() not in seen:
                            out.append(phrase)
                            seen.add(phrase.lower())
                            if len(out) >= max_keywords:
                                break
                    if out:
                        self._cache_set(cache_key, out)
                        return out
        except Exception as e:
            print(f"DDG suggestions failed: {e}")

        # Последний fallback: простые варианты на основе текста
        fallback = []
        q = query.strip()
        if q:
            fallback.append(q)
            words = [w for w in q.split() if len(w) > 2]
            if len(words) >= 2:
                fallback.append(" ".join(words[:2]))
            if len(words) >= 3:
                fallback.append(" ".join(words[:3]))
            fallback.append(q + " новости")
            out = []
            seen = set()
            for p in fallback:
                low = p.lower()
                if low not in seen:
                    seen.add(low)
                    out.append(p)
                    if len(out) >= max_keywords:
                        break
            self._cache_set(cache_key, out)
            return out
        
        return []

    def fact_check(self, text: str, title: str = "", timeout: float = 12.0) -> str:
        """Синхронная проверка фактов."""
        if not text:
            return "Нет текста для фактчекинга."
        if not self.client:
            return "Фактчекинг недоступен: не настроен API ключ.\n\nУстановите переменную окружения OPENROUTER_API_KEY или OPENAI_API_KEY."
        
        prompt = f"Проведи быструю проверку фактов для заголовка: {title}\n\n{text[:8000]}\n\nКратко: укажи 3–5 ключевых утверждений и риск (НИЗКИЙ/СРЕДНИЙ/ВЫСОКИЙ)."
        last_err = None
        
        for model in self.models:
            try:
                future = self._executor.submit(self._call_model, model, prompt, 400, 0.2)
                result = future.result(timeout=timeout)
                if result:
                    return result
            except Exception as e:
                last_err = e
                print(f"Fact-check model {model} failed: {e}")
        
        if last_err:
            msg = str(last_err).replace("\n", " ")
            return f"Ошибка фактчекинга: {msg[:180]}"
        return "Ошибка фактчекинга: модель не ответила."


llm_client = FastLLMClient()
