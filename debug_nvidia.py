from news_search_core import get_news_with_content, fetch_article

def debug_nvidia():
    query = "Nvidia третье поколение"
    print(f"Searching for: {query}")
    results = get_news_with_content(query, max_results=10)
    
    if not results:
        print("No results found.")
        return

    target_snippet = "Nvidia, известная в ИИ-мире своими графическими процессорами"
    
    found_item = None
    for item in results:
        if target_snippet in item.get('description', '') or target_snippet in item.get('title', ''):
            found_item = item
            break
            
    if not found_item:
        print("Target article not found in top 10 results.")
        # Print first result anyway to test extraction
        found_item = results[0]
        print("Testing on first result instead.")

    print(f"Found article: {found_item['title']}")
    print(f"Link: {found_item['link']}")
    print(f"Description: {found_item['description']}")
    
    print("\nFetching full text (Simulating NO TRAFILATURA)...")
    
    # Hack to disable trafilatura in news_search_core for this test
    import news_search_core
    news_search_core.TRAFILATURA_AVAILABLE = False
    
    res = fetch_article(found_item['link'])
    full_text = res.get('full_text', '')
    
    print(f"Full text length: {len(full_text)}")
    print(f"Full text content (first 500 chars):\n{full_text[:500]}")

if __name__ == "__main__":
    debug_nvidia()
