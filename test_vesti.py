from news_search_core import get_news_with_content, fetch_article

def test_vesti():
    query = "искусственный интеллект прямая линия путин"
    print(f"Searching for: {query}")
    results = get_news_with_content(query, max_results=3)
    
    if not results:
        print("No results found.")
        return

    for item in results:
        print(f"\nTitle: {item['title']}")
        print(f"Link: {item['link']}")
        print(f"Description (snippet): {item['description']}")
        
        print("Fetching full text...")
        res = fetch_article(item['link'])
        full_text = res.get('full_text', '')
        
        print(f"Extracted text length: {len(full_text)}")
        print(f"Extracted text start: {full_text[:200]}")
        
        if "Последние новости на сайте Вести" in full_text:
            print("!!! FOUND THE PROBLEMATIC TEXT !!!")

if __name__ == "__main__":
    test_vesti()
