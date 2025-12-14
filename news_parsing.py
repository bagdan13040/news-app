"""Консольный поиск новостей через DuckDuckGo"""

from __future__ import annotations

import argparse
import sys
import textwrap
from typing import List, Dict, Optional

import requests

from news_search_core import fetch_article_text, search_duckduckgo


def print_results(results: List[Dict[str, str]]) -> None:
	for index, item in enumerate(results, start=1):
		title = item.get("title", "")
		snippet = item.get("snippet", "")
		link = item.get("link", "")
		print(f"[{index}] {title}")
		if snippet:
			print(textwrap.fill(snippet, width=80, initial_indent="    ", subsequent_indent="    "))
		if link:
			print(f"    {link}")
		print()


def prompt_selection(count: int) -> Optional[int]:
	while True:
		choice = input("Введите номер новости для полного текста или q для выхода: ").strip().lower()
		if choice in {"q", "quit", "exit"}:
			return None
		if not choice.isdigit():
			print("Пожалуйста, укажите число или q.")
			continue
		selected = int(choice)
		if 1 <= selected <= count:
			return selected - 1
		print("Номер вне диапазона. Попробуйте снова.")


def main() -> None:
	parser = argparse.ArgumentParser(description="Поиск новостей через DuckDuckGo")
	parser.add_argument("query", help="Фраза для поиска новостей")
	parser.add_argument("--limit", "-n", type=int, default=6, help="Сколько превью показать")
	args = parser.parse_args()

	try:
		results = search_duckduckgo(args.query, max_results=args.limit)
	except requests.RequestException as exc:
		print(f"Ошибка при запросе DuckDuckGo: {exc}")
		sys.exit(1)

	if not results:
		print("По вашему запросу ничего не найдено.")
		return

	print_results(results)

	while True:
		selected_idx = prompt_selection(len(results))
		if selected_idx is None:
			print("Выход.")
			return

		selected = results[selected_idx]
		link = selected.get("link", "")
		if not link:
			print("Ссылка недоступна.")
			continue

		print(f"Загружаем полную статью: {link}")
		try:
			full_text = fetch_article_text(link)
		except requests.RequestException as exc:
			print(f"Не удалось загрузить статью: {exc}")
			continue

		if not full_text:
			print("Не удалось извлечь текст из статьи.")
			continue

		print("\n" + "=" * 40 + "\n")
		print(textwrap.fill(full_text, width=80))
		print("\n" + "=" * 40 + "\n")


if __name__ == "__main__":
	main()
