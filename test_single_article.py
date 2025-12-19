"""Тест парсинга одной статьи"""
import sys
from news_search_core import fetch_article

# Тестируем на реальной новости
url = "https://zn.ua/TECHNOLOGIES/zahrjaznjaet-kak-nju-jork-i-pet-bolshe-chem-ljudi-vlijanie-iskusstvennoho-intellekta-na-klimat.html"

print(f"Загружаю: {url}")
article = fetch_article(url)

full_text = article.get('full_text', '')
image = article.get('image')

print(f"\nДлина текста: {len(full_text)} символов")
print(f"Картинка: {image}")
print(f"\nПОЛНЫЙ ТЕКСТ:")
print("=" * 80)
print(full_text)
print("=" * 80)
