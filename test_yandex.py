from news_search_core import get_news_with_content

print("Testing Bing News...")
results = get_news_with_content('технологии', max_results=5, fetch_content=False, source='bing')
print(f'\nFound {len(results)} results from Bing:')
for i, r in enumerate(results, 1):
    print(f"{i}. {r['title'][:70]}... - {r['source']}")

print("\n" + "="*80)
print("Testing Google News...")
results = get_news_with_content('технологии', max_results=5, fetch_content=False, source='google')
print(f'\nFound {len(results)} results from Google:')
for i, r in enumerate(results, 1):
    print(f"{i}. {r['title'][:70]}... - {r['source']}")

print("\n" + "="*80)
print("Testing ALL sources (Bing + Google)...")
results = get_news_with_content('ии', max_results=8, fetch_content=False, source='all')
print(f'\nFound {len(results)} results from ALL:')
for i, r in enumerate(results, 1):
    print(f"{i}. {r['title'][:70]}... - {r['source']}")
