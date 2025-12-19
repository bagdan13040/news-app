import sys
import os
from news_search_core import get_news_with_content, fetch_article

def test_search_flow():
    print("Searching for 'новости'...")
    # Use fast search (no full text initially)
    results = get_news_with_content("новости", max_results=5)
    
    print(f"Found {len(results)} results.")
    
    for i, item in enumerate(results):
        print(f"\n--- Result {i+1} ---")
        print(f"Title: {item['title']}")
        print(f"URL: {item['link']}")
        
        # Now fetch full text
        print("Fetching full text...")
        article = fetch_article(item['link'])
        full_text = article.get('full_text', '')
        
        print(f"Full text length: {len(full_text)}")
        if len(full_text) < 200:
            print("WARNING: Text is very short!")
            print(f"Text content: {full_text}")
        else:
            print(f"First 200 chars: {full_text[:200]}...")

if __name__ == "__main__":
    test_search_flow()
