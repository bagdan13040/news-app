"""Тестирование парсинга новостей"""
import sys
from news_search_core import get_news_with_content, fetch_article

# Получаем новости
print("Ищем новости...")
results = get_news_with_content("искусственный интеллект", max_results=3)

if not results:
    print("Новости не найдены")
    sys.exit(1)

print(f"\nНайдено {len(results)} новостей\n" + "="*80)

for i, item in enumerate(results, 1):
    print(f"\n{i}. {item['title']}")
    print(f"Ссылка: {item['link']}")
    print(f"Картинка из RSS: {item.get('image')}")
    print(f"Описание из RSS: {item.get('description')[:150] if item.get('description') else 'нет'}")
    
    # Теперь пробуем загрузить полный текст
    print(f"\nЗагружаю полный текст с сайта...")
    article = fetch_article(item['link'])
    
    full_text = article.get('full_text', '')
    image = article.get('image')
    
    print(f"Длина полного текста: {len(full_text)} символов")
    print(f"Картинка с сайта: {image}")
    print(f"\nПервые 500 символов текста:")
    print("-" * 80)
    print(full_text[:500] if full_text else "ПУСТО!")
    print("-" * 80)
    
    if i == 1:  # Только первую статью проверяем детально
        break
