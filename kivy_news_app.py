"""Kiwi/KivyMD клиент для поиска и чтения новостей."""

from __future__ import annotations

import threading
import datetime
from functools import partial
from typing import List, Dict

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.utils import platform
from kivy.graphics import Line, Color, RoundedRectangle
from kivy.uix.image import AsyncImage
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.gridlayout import GridLayout
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDRaisedButton, MDFlatButton, MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.screen import Screen
from kivymd.uix.screenmanager import ScreenManager
from kivymd.uix.textfield import MDTextField
from kivymd.uix.card import MDCard
from kivymd.uix.list import MDList
from kivymd.uix.list import OneLineIconListItem
# MDToolbar may not be available in older KivyMD builds; use a simple box toolbar instead
from kivymd.uix.navigationdrawer import MDNavigationLayout, MDNavigationDrawer
from kivymd.toast import toast
from kivymd.uix.dialog import MDDialog

from news_search_core import get_news_with_content
from backend import get_weather, get_financial_data, get_google_trends
from llm_integration import llm_client


class ResultCard(MDCard):
    """Карточка сниппета с кнопкой загрузки полного текста."""

    def __init__(self, payload: Dict[str, str], on_read: object, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = "14dp"
        self.spacing = "10dp"
        self.radius = [12]
        self.size_hint_y = None
        self.adaptive_height = True
        self.md_bg_color = (1, 1, 1, 1)
        self.link = payload.get("link", "")
        image_url = payload.get("image")

        if image_url:
            preview = AsyncImage(
                source=image_url,
                size_hint_y=None,
                height="160dp",
                allow_stretch=True,
                keep_ratio=True,
            )
            self.add_widget(preview)

        title = payload.get("title", "Без заголовка")
        snippet = payload.get("snippet") or payload.get("description") or payload.get("full_text") or ""

        title_label = MDLabel(
            text=title,
            theme_text_color="Custom",
            text_color=(0.05, 0.05, 0.05, 1),
            font_style="H6",
            bold=True,
            size_hint_y=None,
        )
        title_label.bind(texture_size=lambda inst, val: setattr(inst, "height", val[1]))
        title_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        self.add_widget(title_label)

        snippet_label = MDLabel(
            text=snippet or "Прочитать полную статью",
            theme_text_color="Custom",
            text_color=(0.5, 0.5, 0.5, 1),
            font_size="12sp",
            size_hint_y=None,
        )
        snippet_label.bind(texture_size=lambda inst, val: setattr(inst, "height", val[1]))
        snippet_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        self.add_widget(snippet_label)

        button_row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="36dp")
        button_row.add_widget(MDLabel(size_hint_x=None, width="0dp"))
        action_btn = MDRaisedButton(
            text="→",
            font_size="22sp",
            size_hint=(None, 1),
            width="52dp",
            md_bg_color=(0.15, 0.55, 0.75, 1),
        )
        action_btn.bind(on_release=lambda *_: on_read(self.link))
        button_row.add_widget(action_btn)
        self.add_widget(button_row)


class SearchScreen(Screen):
    """Экран поиска новостей."""

    def __init__(self, app: NewsSearchApp, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.app = app
        self.article_cache: Dict[str, str] = {}
        self.article_payloads: Dict[str, Dict[str, str]] = {}
        layout = MDBoxLayout(orientation="vertical", padding="12dp", spacing="12dp")

        # Добавим отступ сверху, чтобы toolbar не перекрывал содержимое, если используется
        layout.padding = (12, 12, 12, 12)

        # Center the search row horizontally using an AnchorLayout
        # Avoid fixed widths on mobile: make the row expand to available width.
        search_row = MDBoxLayout(size_hint=(1, None), height=dp(56), spacing=dp(10))
        self.query_field = MDTextField(hint_text="Что искать?", mode="rectangle")
        # Let the text field take remaining space and use a compact icon button for search
        self.query_field.size_hint_x = 1
        search_button = MDIconButton(icon="magnify", icon_size="24sp", size_hint=(None, 1), width=dp(56))
        search_button.bind(on_release=self.on_search)
        search_row.add_widget(self.query_field)
        search_row.add_widget(search_button)
        search_anchor = AnchorLayout(size_hint_y=None, height=dp(56), padding=(dp(0), dp(0), dp(0), dp(0)))
        search_anchor.add_widget(search_row)

        self.status_label = MDLabel(
            text="Введите тему новости и нажмите «Искать».",
            halign="left",
            size_hint_y=None,
            height="28dp",
        )

        self.list_scroll = MDScrollView()
        self.results_list = MDList(spacing="12dp", padding="10dp")
        self.list_scroll.add_widget(self.results_list)

        layout.add_widget(search_anchor)
        layout.add_widget(self.status_label)
        layout.add_widget(self.list_scroll)
        self.add_widget(layout)

    def on_search(self, _instance: object) -> None:
        query = self.query_field.text.strip()
        if not query:
            toast("Укажите текст запроса.")
            return
        self.status_label.text = "Ищу новости..."
        self.results_list.clear_widgets()
        threading.Thread(target=self._perform_search, args=(query,), daemon=True).start()

    def _perform_search(self, query: str) -> None:
        try:
            # Получаем основные результаты
            print(f"[SEARCH] Searching for: {query}")
            # IMPORTANT: keep search fast on mobile; full text is fetched on demand.
            results = get_news_with_content(query, max_results=6)
            print(f"[SEARCH] Found {len(results)} initial results")

            # Show initial results ASAP.
            Clock.schedule_once(partial(self._populate_results, results, query), 0)
            
        except Exception as exc:
            print(f"[SEARCH] Error: {exc}")
            import traceback
            traceback.print_exc()
            Clock.schedule_once(partial(self._set_status, f"Ошибка поиска: {exc}"), 0)
            return

        # Background enrichment: synonyms-based extra results (non-blocking UI).
        try:
            print(f"[SYNONYMS] Generating synonyms for: {query}")
            synonyms = llm_client.generate_related_keywords(query, max_keywords=3, timeout=1.5)
            synonyms = [s for s in synonyms if s and s.lower() != query.lower()]
            if not synonyms:
                return
            print(f"[SYNONYMS] Got synonyms: {synonyms}")

            extra: List[Dict[str, str]] = []
            for synonym in synonyms:
                print(f"[SYNONYMS] Searching for synonym: {synonym}")
                syn_results = get_news_with_content(synonym, max_results=2)
                extra.extend(syn_results)

            if extra:
                Clock.schedule_once(partial(self._append_results, extra), 0)
        except Exception as e:
            print(f"[SYNONYMS] Error: {e}")
            import traceback
            traceback.print_exc()

    def _set_status(self, text: str, _: float) -> None:
        self.status_label.text = text

    def _populate_results(self, results: List[Dict[str, str]], query: str, _: float) -> None:
        if not results:
            self.status_label.text = f"По запросу \"{query}\" ничего не найдено."
            return
        # reset list (new search)
        self.results_list.clear_widgets()
        self.status_label.text = f"Найдено {len(results)} результата."
        for idx, payload in enumerate(results):
            # Контейнер для каждой карточки
            card_container = MDBoxLayout(
                orientation="vertical",
                size_hint_y=None,
                adaptive_height=True,
                padding="4dp",
                spacing="4dp",
            )
            card = ResultCard(payload, on_read=self.app.show_article)
            card_container.add_widget(card)
            self.results_list.add_widget(card_container)

            link = payload.get("link", "")
            if link:
                self.article_payloads[link] = payload
                cached_text = payload.get("full_text") or payload.get("description") or ""
                if cached_text:
                    self.article_cache[link] = cached_text
            
            # Добавляем разделитель между карточками (кроме последней)
            if idx < len(results) - 1:
                separator = MDBoxLayout(size_hint_y=None, height="6dp")
                self.results_list.add_widget(separator)

    def _append_results(self, results: List[Dict[str, str]], _dt: float = 0) -> None:
        """Append extra results (e.g. synonyms) without clearing existing ones."""
        if not results:
            return

        # De-duplicate by link (avoid UI spam)
        existing = set(self.article_payloads.keys())
        added = 0
        for payload in results:
            link = payload.get("link", "")
            if link and link in existing:
                continue
            if link:
                existing.add(link)

            card_container = MDBoxLayout(
                orientation="vertical",
                size_hint_y=None,
                adaptive_height=True,
                padding="4dp",
                spacing="4dp",
            )
            card = ResultCard(payload, on_read=self.app.show_article)
            card_container.add_widget(card)
            self.results_list.add_widget(card_container)

            if link:
                self.article_payloads[link] = payload
                cached_text = payload.get("full_text") or payload.get("description") or ""
                if cached_text:
                    self.article_cache[link] = cached_text
            added += 1

        if added:
            self.status_label.text = f"Найдено {len(self.article_payloads)} результата."

    def _prefetch_articles(self, results: List[Dict[str, str]]) -> None:
        for payload in results:
            link = payload.get("link", "")
            full_text = payload.get("full_text", "")
            if link and full_text:
                self.article_cache[link] = full_text


class HomeScreen(Screen):
    """Домашний экран с наборами виджетов: погода, рынки, тренды, категории."""

    def __init__(self, app: NewsSearchApp, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.app = app
        self.scroll = None
        self.root_layout = None
        
        # Создаем начальный UI с загрузкой, затем асинхронно загрузим данные
        self._build_loading_ui()
        threading.Thread(target=self._fetch_and_build, daemon=True).start()

    def _build_loading_ui(self):
        """Показать экран загрузки."""
        scroll = MDScrollView()
        layout = MDBoxLayout(orientation="vertical", padding="16dp", spacing="14dp", size_hint_y=None)
        layout.bind(minimum_height=layout.setter("height"))
        layout.add_widget(MDLabel(text="Загружаю данные...", halign="center", font_style="H6"))
        scroll.add_widget(layout)
        self.add_widget(scroll)
    
    def _trigger_search(self, query: str):
        """Запустить поиск в SearchScreen."""
        search_screen = self.app.search_screen
        search_screen.query_field.text = query
        search_screen.on_search(None)

    def _fetch_and_build(self):
        """Загрузить данные из API и построить UI."""
        # Координаты Уфы: 54.74, 55.97
        weather_data = get_weather(54.74, 55.97)
        # Запрашиваем все финансовые инструменты (функция использует дефолтный список из backend.py)
        fin_data = get_financial_data()
        trending_data = get_google_trends()
        
        print(f"[FINANCE] Got data for {len(fin_data)} instruments: {list(fin_data.keys())}")
        
        Clock.schedule_once(lambda dt: self._build_ui(weather_data, fin_data, trending_data), 0)

    def _build_ui(self, weather_data: dict, fin_data: dict, trending_data: list, _dt=0):
        """Построить UI с реальными данными."""
        self.clear_widgets()
        
        now = datetime.datetime.now()
        months = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
            7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
        }
        days = {0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье"}
        ru_date = f"{now.day} {months[now.month]}, {days[now.weekday()]}"

        # Обработка погоды
        temp = weather_data.get('temperature', 0)
        wind = weather_data.get('windspeed', 0)
        w_code = weather_data.get('weathercode', 0)
        cond = "Ясно" if w_code == 0 else "Облачно" if w_code in [1,2,3] else "Дождь" if w_code in [61,63,65] else "Облачно"
        icon = "weather-sunny" if w_code == 0 else "weather-cloudy" if w_code in [1,2,3] else "weather-rainy" if w_code in [61,63,65] else "weather-cloudy"
        weather = {"city": "Уфа", "temp": f"{int(temp)}°", "cond": cond, "wind": f"{int(wind)} м/с", "hum": "45%", "icon": icon}
        
        # Обработка финансов
        print(f"[FINANCE BUILD] Processing fin_data: {fin_data}")
        usd_rate = fin_data.get('RUB=X', 0.0)
        eur_rate = fin_data.get('EURRUB=X', 0.0)
        print(f"[FINANCE BUILD] USD rate: {usd_rate}, EUR rate: {eur_rate}")
        
        # Формируем список всех инструментов (всегда показываем, даже если нет данных)
        instruments = []
        
        # Валюты
        instruments.append(("USD", f"{usd_rate:.2f} ₽" if usd_rate else "—", "currency-usd"))
        instruments.append(("EUR", f"{eur_rate:.2f} ₽" if eur_rate else "—", "currency-eur"))
        
        # Акции (в долларах)
        for symbol, name in [("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("GOOGL", "Google"), 
                              ("MSFT", "Microsoft"), ("TSLA", "Tesla"), ("AMZN", "Amazon")]:
            price_usd = fin_data.get(symbol, 0.0)
            print(f"[FINANCE BUILD] {name} ({symbol}): ${price_usd}")
            if price_usd:
                instruments.append((name, f"${price_usd:.2f}", "chart-line"))
            else:
                instruments.append((name, "—", "chart-line"))
        
        # Криптовалюты (в долларах)
        for symbol, name in [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum")]:
            price = fin_data.get(symbol, 0.0)
            print(f"[FINANCE BUILD] {name} ({symbol}): ${price}")
            if price:
                instruments.append((name, f"${int(price):,}", "currency-btc"))
            else:
                instruments.append((name, "—", "currency-btc"))
        
        currencies = instruments
        print(f"[FINANCE BUILD] Total instruments created: {len(currencies)}")
        
        # Тренды - показываем все загруженные
        trending = trending_data if trending_data else [
            {"tag": "#AI", "name": "Искусственный интеллект", "change": "+2.1%"},
            {"tag": "#Tech", "name": "Технологии и инновации", "change": "+1.5%"},
            {"tag": "#World", "name": "Мировые события", "change": "+0.8%"},
        ]
        
        categories = [
            ("laptop", "Техно"),
            ("soccer", "Спорт"),
            ("briefcase", "Бизнес"),
            ("atom", "Наука"),
            ("movie", "Кино"),
            ("music", "Музыка"),
        ]

        scroll = MDScrollView()
        root = MDBoxLayout(orientation="vertical", padding="16dp", spacing="14dp", size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))

        header = MDBoxLayout(orientation="vertical", spacing="4dp", size_hint_y=None, height="80dp")
        header.add_widget(MDLabel(text="Добро пожаловать!", font_style="H5", halign="left"))
        header.add_widget(MDLabel(text=f"Сегодня {ru_date}", halign="left", theme_text_color="Secondary"))
        root.add_widget(header)

        # Погода
        weather_card = MDCard(padding="14dp", radius=[14], md_bg_color=(0.15, 0.35, 0.65, 1), size_hint_y=None, height="180dp")
        weather_layout = MDBoxLayout(orientation="vertical", spacing="8dp")
        
        # Город с иконкой
        weather_top = MDBoxLayout(orientation="horizontal", spacing="4dp", size_hint_y=None, height="28dp")
        weather_top.add_widget(MDIconButton(icon="map-marker", icon_size="18sp", theme_text_color="Custom", text_color=(1,1,1,0.9), size_hint_x=None, width="32dp"))
        weather_top.add_widget(MDLabel(text=weather["city"], theme_text_color="Custom", text_color=(1,1,1,0.95), bold=True, halign="left", size_hint_x=None, width="80dp"))
        weather_top.add_widget(MDLabel(text=""))  # Spacer
        weather_layout.add_widget(weather_top)
        
        # Температура и состояние
        weather_mid = MDBoxLayout(orientation="horizontal", spacing="16dp", size_hint_y=None, height="64dp")
        temp_label = MDLabel(text=weather["temp"], font_style="H3", theme_text_color="Custom", text_color=(1,1,1,1), size_hint_x=None, width="120dp", halign="left")
        weather_mid.add_widget(temp_label)
        
        # Состояние и иконка в вертикальном контейнере
        cond_container = MDBoxLayout(orientation="vertical", spacing="4dp")
        cond_label = MDLabel(text=weather["cond"], theme_text_color="Custom", text_color=(1,1,1,0.9), halign="left", valign="bottom", size_hint_y=None, height="28dp")
        cond_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        cond_container.add_widget(cond_label)
        cond_container.add_widget(MDLabel(text=""))  # Spacer
        weather_mid.add_widget(cond_container)
        
        # Иконка погоды справа
        icon_anchor = AnchorLayout(anchor_x="right", anchor_y="center")
        icon_anchor.add_widget(MDIconButton(icon=weather["icon"], icon_size="42sp", theme_text_color="Custom", text_color=(1,1,1,0.95)))
        weather_mid.add_widget(icon_anchor)
        weather_layout.add_widget(weather_mid)
        
        # Ветер и влажность
        weather_bot = MDBoxLayout(orientation="horizontal", spacing="16dp", size_hint_y=None, height="28dp")
        weather_bot.add_widget(MDLabel(text=f"Ветер {weather['wind']}", theme_text_color="Custom", text_color=(1,1,1,0.85), halign="left"))
        weather_bot.add_widget(MDLabel(text=f"Влажность {weather['hum']}", theme_text_color="Custom", text_color=(1,1,1,0.85), halign="right"))
        weather_layout.add_widget(weather_bot)
        weather_card.add_widget(weather_layout)
        root.add_widget(weather_card)

        # Рынки с горизонтальной прокруткой (свайпом)
        market_card = MDCard(padding="14dp", radius=[14], md_bg_color=(1,1,1,1), size_hint_y=None, height="200dp")
        market_outer = MDBoxLayout(orientation="vertical", spacing="6dp")
        
        # Заголовок
        market_header = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="28dp")
        market_header.add_widget(MDLabel(text="Рынки", font_style="H6", halign="left", theme_text_color="Primary"))
        market_outer.add_widget(market_header)
        
        # Горизонтальный скролл для всех инструментов
        market_scroll = MDScrollView(do_scroll_x=True, do_scroll_y=False, size_hint_y=None, height="140dp", bar_width=0)
        market_container = MDBoxLayout(orientation="horizontal", spacing="10dp", size_hint=(None, None), height="130dp", padding="0dp")
        market_container.bind(minimum_width=market_container.setter("width"))
        
        # Добавляем все инструменты
        for code, val, icon in currencies:
            chip = MDCard(padding="10dp", radius=[10], md_bg_color=(0.95,0.97,1,1), size_hint=(None, None), width="110dp", height="130dp")
            chip_layout = MDBoxLayout(orientation="vertical", spacing="2dp")
            chip_layout.add_widget(MDIconButton(icon=icon, icon_size="24sp", size_hint_y=None, height="32dp", disabled=True, theme_icon_color="Custom", icon_color=(0.3, 0.5, 0.8, 1)))
            chip_layout.add_widget(MDLabel(text=code, font_style="Subtitle1", halign="left", theme_text_color="Secondary", size_hint_y=None, height="20dp"))
            chip_layout.add_widget(MDLabel(text=val, font_style="H6", halign="left", theme_text_color="Primary", size_hint_y=None, height="60dp"))
            chip.add_widget(chip_layout)
            market_container.add_widget(chip)
        
        market_scroll.add_widget(market_container)
        market_outer.add_widget(market_scroll)
        market_card.add_widget(market_outer)
        root.add_widget(market_card)

        # Тренды с навигацией
        trend_card = MDCard(padding="14dp", radius=[14], md_bg_color=(1,1,1,1), size_hint_y=None, height="260dp")
        trend_outer = MDBoxLayout(orientation="vertical", spacing="6dp")
        trend_header = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="28dp")
        trend_header.add_widget(MDIconButton(icon="trending-up", icon_size="20sp", size_hint_x=None, width="32dp"))
        trend_header.add_widget(MDLabel(text="В тренде", font_style="H6", halign="left", size_hint_y=None, height="28dp"))
        trend_outer.add_widget(trend_header)
        
        # Контейнер для отображения трендов
        self.trend_container = MDBoxLayout(orientation="vertical", spacing="6dp", size_hint_y=None, height="180dp")
        self.trending_items = trending
        self.trend_index = 0
        
        def on_trend_click(query_text):
            """Запустить поиск по тренду."""
            self.app._go_to("search")
            Clock.schedule_once(lambda dt: self._trigger_search(query_text), 0.2)
        
        def update_trends():
            """Обновить отображаемые тренды."""
            self.trend_container.clear_widgets()
            # Показываем 2 тренда за раз
            visible_count = 2
            start = self.trend_index
            end = min(start + visible_count, len(self.trending_items))
            
            for i in range(start, end):
                item = self.trending_items[i]
                row_container = MDCard(
                    padding="10dp",
                    radius=[8],
                    md_bg_color=(0.98, 0.98, 1, 1),
                    size_hint_y=None,
                    height="82dp",
                    ripple_behavior=True
                )
                row = MDBoxLayout(orientation="vertical", spacing="4dp")
                
                top_row = MDBoxLayout(orientation="horizontal", spacing="6dp", size_hint_y=None, height="20dp")
                tag_label = MDLabel(
                    text=item["tag"],
                    theme_text_color="Secondary",
                    size_hint_x=None,
                    width="50dp",
                    halign="left",
                    font_size="10sp"
                )
                top_row.add_widget(tag_label)
                change_label = MDLabel(
                    text=item["change"],
                    halign="right",
                    theme_text_color="Secondary",
                    font_size="10sp"
                )
                top_row.add_widget(change_label)
                row.add_widget(top_row)
                
                name_label = MDLabel(
                    text=item["name"],
                    halign="left",
                    valign="top",
                    theme_text_color="Primary",
                    font_size="13sp",
                    size_hint_y=None,
                    height="52dp"
                )
                name_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
                row.add_widget(name_label)
                
                row_container.add_widget(row)
                query = item["name"]
                row_container.bind(on_release=lambda x, q=query: on_trend_click(q))
                self.trend_container.add_widget(row_container)
        
        def scroll_up(*args):
            if self.trend_index > 0:
                self.trend_index -= 1
                update_trends()
        
        def scroll_down(*args):
            if self.trend_index < len(self.trending_items) - 2:
                self.trend_index += 1
                update_trends()
        
        update_trends()
        trend_outer.add_widget(self.trend_container)
        
        # Кнопки навигации
        nav_buttons = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="32dp")
        up_btn = MDIconButton(icon="chevron-up", icon_size="24sp", on_release=scroll_up)
        down_btn = MDIconButton(icon="chevron-down", icon_size="24sp", on_release=scroll_down)
        nav_buttons.add_widget(MDLabel(text=""))  # Spacer
        nav_buttons.add_widget(up_btn)
        nav_buttons.add_widget(down_btn)
        trend_outer.add_widget(nav_buttons)
        
        trend_card.add_widget(trend_outer)
        root.add_widget(trend_card)

        # Категории
        cat_card = MDCard(padding="14dp", radius=[14], md_bg_color=(1,1,1,1), size_hint_y=None, height="260dp")
        cat_layout = MDBoxLayout(orientation="vertical", spacing="10dp")
        cat_layout.add_widget(MDLabel(text="Категории", font_style="H6", halign="center", size_hint_y=None, height="28dp"))
        
        grid_container = AnchorLayout(anchor_x="center", anchor_y="center")
        grid = GridLayout(cols=3, spacing=10, padding=0, size_hint=(None, None), width="345dp", height="200dp")
        
        def on_category_click(category_name):
            """Запустить поиск по категории."""
            self.app._go_to("search")
            Clock.schedule_once(lambda dt: self._trigger_search(category_name), 0.2)
        
        # Цвета для каждой категории
        category_colors = [
            (0.90, 0.95, 1.0, 1),    # Голубой - Техно
            (0.95, 1.0, 0.90, 1),    # Зеленый - Спорт
            (1.0, 0.95, 0.85, 1),    # Оранжевый - Бизнес
            (0.95, 0.90, 1.0, 1),    # Фиолетовый - Наука
            (1.0, 0.90, 0.90, 1),    # Розовый - Кино
            (0.98, 0.98, 0.85, 1),   # Желтый - Музыка
        ]
        
        for idx, (icon_name, label) in enumerate(categories):
            color = category_colors[idx % len(category_colors)]
            cell = MDCard(padding="10dp", radius=[12], md_bg_color=color, size_hint=(None, None), width="110dp", height="96dp", ripple_behavior=True)
            cell_layout = MDBoxLayout(orientation="vertical", spacing="2dp")
            
            # Центрируем иконку
            icon_container = AnchorLayout(anchor_x="center", anchor_y="center", size_hint_y=None, height="48dp")
            icon_btn = MDIconButton(icon=icon_name, icon_size="32sp", disabled=True, theme_icon_color="Custom", icon_color=(0.3, 0.3, 0.3, 1))
            icon_container.add_widget(icon_btn)
            cell_layout.add_widget(icon_container)
            
            # Центрируем текст
            label_container = AnchorLayout(anchor_x="center", anchor_y="center", size_hint_y=None, height="36dp")
            text_label = MDLabel(text=label, halign="center", theme_text_color="Primary", font_size="13sp", size_hint=(1, None), height="36dp")
            text_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
            label_container.add_widget(text_label)
            cell_layout.add_widget(label_container)
            
            cell.add_widget(cell_layout)
            cell.bind(on_release=lambda x, cat=label: on_category_click(cat))
            grid.add_widget(cell)
        
        grid_container.add_widget(grid)
        cat_layout.add_widget(grid_container)
        cat_card.add_widget(cat_layout)
        root.add_widget(cat_card)

        scroll.add_widget(root)
        self.add_widget(scroll)


class ArticleScreen(Screen):
    """Экран отображения полного текста статьи."""

    def __init__(self, app: NewsSearchApp, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.app = app
        self.current_article = None
        layout = MDBoxLayout(orientation="vertical", padding="12dp", spacing="12dp")

        top_bar = MDBoxLayout(size_hint_y=None, height="56dp", spacing="10dp")
        back_button = MDIconButton(icon="arrow-left", icon_size="28sp")
        back_button.bind(on_release=lambda *_: self.app.go_back())
        title = MDLabel(text="Полный текст", theme_text_color="Primary", font_style="H6")
        fact_check_button = MDIconButton(icon="shield-check", icon_size="24sp")
        fact_check_button.bind(on_release=self.show_fact_check)
        top_bar.add_widget(back_button)
        top_bar.add_widget(title)
        top_bar.add_widget(fact_check_button)

        self.image = AsyncImage(
            source="",
            size_hint_y=None,
            height="0dp",
            allow_stretch=True,
            keep_ratio=True,
        )

        self.scroll = MDScrollView()
        self.text_label = MDLabel(
            text="Загружаю статью...",
            valign="top",
            halign="left",
            size_hint_y=None,
        )
        # CRITICAL: Bind texture_size to height for dynamic resizing
        self.text_label.bind(
            texture_size=lambda instance, value: setattr(instance, "height", value[1])
        )
        # CRITICAL: Bind width to text_size for proper text wrapping
        self.text_label.bind(
            width=lambda instance, value: setattr(instance, "text_size", (value, None))
        )
        self.scroll.add_widget(self.text_label)

        layout.add_widget(top_bar)
        layout.add_widget(self.image)
        layout.add_widget(self.scroll)
        self.add_widget(layout)

    def set_article_text(self, text: str, image_url: str = "", _: float = 0) -> None:
        # Format text with proper paragraph breaks
        formatted_text = "\n\n".join(text.split("\n\n"))
        
        # CRITICAL: Set text_size BEFORE setting text to ensure proper wrapping
        self.text_label.text_size = (self.text_label.width, None)
        self.text_label.text = formatted_text
        self.text_label.font_size = "16sp"
        self.text_label.line_height = 1.5
        
        # Force immediate height recalculation based on texture
        self.text_label.texture_update()
        self.text_label.height = self.text_label.texture_size[1]

        if image_url:
            self.image.source = image_url
            self.image.height = "220dp"
            self.image.opacity = 1
        else:
            self.image.source = ""
            self.image.height = "0dp"
            self.image.opacity = 0

        # Всегда показываем начало статьи
        def _scroll_to_top(*_args: object) -> None:
            try:
                self.scroll.scroll_y = 1
            except Exception:
                pass

        Clock.schedule_once(_scroll_to_top, 0)

    def show_fact_check(self, *args):
        """Показать результат проверки фактов."""
        if not self.current_article:
            toast("Нет статьи для проверки")
            return
        
        # Показываем диалог загрузки
        loading_dialog = MDDialog(
            title="Проверка фактов",
            text="Анализирую статью...\n\nЭто может занять несколько секунд.",
            size_hint=(0.8, None),
            height="180dp",
        )
        loading_dialog.open()
        
        def do_fact_check(*args):
            try:
                title = self.current_article.get("title", "")
                text = self.current_article.get("full_text", "")
                result = llm_client.fact_check(text, title, timeout=15.0)
                
                def show_result(*args):
                    loading_dialog.dismiss()
                    result_dialog = MDDialog(
                        title="Результат проверки фактов",
                        text=result,
                        size_hint=(0.9, None),
                        buttons=[
                            MDFlatButton(
                                text="Закрыть",
                                on_release=lambda x: result_dialog.dismiss()
                            )
                        ],
                    )
                    result_dialog.open()
                
                Clock.schedule_once(show_result, 0)
            except Exception as e:
                err_msg = str(e)[:100]

                def show_error(*args):
                    loading_dialog.dismiss()
                    toast(f"Ошибка: {err_msg}")
                Clock.schedule_once(show_error, 0)
        
        # Запускаем в отдельном потоке
        threading.Thread(target=do_fact_check, daemon=True).start()


class NewsSearchApp(MDApp):
    def build(self) -> ScreenManager:
        # IMPORTANT:
        # Do not force Window.size on Android/iOS.
        # On mobile it can lead to letterboxing / strange scaling where the UI
        # occupies only a small portion of the screen.
        if platform not in ("android", "ios"):
            Window.size = (375, 667)
        self.theme_cls.primary_palette = "BlueGray"

        # Navigation layout: toolbar + drawer + screens
        nav_layout = MDNavigationLayout()

        self.screen_manager = ScreenManager()
        self.home_screen = HomeScreen(app=self, name="home")
        self.search_screen = SearchScreen(app=self, name="search")
        self.article_screen = ArticleScreen(app=self, name="article")

        self.screen_manager.add_widget(self.home_screen)
        self.screen_manager.add_widget(self.search_screen)
        self.screen_manager.add_widget(self.article_screen)

        # Toolbar (simple, title centered)
        toolbar = AnchorLayout(size_hint_y=None, height="56dp")
        title_label = MDLabel(text="NewsSearch", theme_text_color="Primary", font_style="H6", halign="center")
        toolbar.add_widget(title_label)

        # Navigation drawer
        self.drawer = MDNavigationDrawer()
        drawer_list = MDList()
        home_item = OneLineIconListItem(text="Home", on_release=lambda *_: self._go_to("home"))
        search_item = OneLineIconListItem(text="Search", on_release=lambda *_: self._go_to("search"))
        drawer_list.add_widget(home_item)
        drawer_list.add_widget(search_item)
        self.drawer.add_widget(drawer_list)

        # MDNavigationLayout expects MDNavigationDrawer and the main content (ScreenManager) as direct children
        # We'll put toolbar inside a container screen so it appears above screens.
        # MDNavigationLayout requires MDNavigationDrawer + ScreenManager as direct children
        # Create a content ScreenManager that will be the MDNavigationLayout child
        content_manager = ScreenManager()

        # Create a screen that holds toolbar + the real screen_manager content
        content_screen = Screen(name="content")
        wrapper_layout = MDBoxLayout(orientation="vertical")
        wrapper_layout.add_widget(toolbar)
        wrapper_layout.add_widget(self.screen_manager)

        # Bottom navigation: full-width bar, icons centered.
        bottom_bar = MDBoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(64),
            padding=(dp(16), 0, dp(16), dp(8)),
        )
        bottom_anchor = AnchorLayout(anchor_x="center", anchor_y="center")
        bottom_icons = MDBoxLayout(
            orientation="horizontal",
            size_hint=(None, None),
            height=dp(48),
            spacing=dp(28),
        )
        bottom_home = MDIconButton(icon="home", icon_size="28sp")
        bottom_home.bind(on_release=lambda *_: self._go_to("home"))
        bottom_search = MDIconButton(icon="magnify", icon_size="28sp")
        bottom_search.bind(on_release=lambda *_: self._go_to("search"))
        bottom_icons.add_widget(bottom_home)
        bottom_icons.add_widget(bottom_search)
        bottom_anchor.add_widget(bottom_icons)
        bottom_bar.add_widget(bottom_anchor)
        wrapper_layout.add_widget(bottom_bar)

        content_screen.add_widget(wrapper_layout)

        content_manager.add_widget(content_screen)

        nav_layout.add_widget(self.drawer)
        nav_layout.add_widget(content_manager)

        return nav_layout

    def on_start(self):
        """Called when the application starts."""
        # Check for trafilatura availability
        try:
            from news_search_core import TRAFILATURA_AVAILABLE
            status = "включен" if TRAFILATURA_AVAILABLE else "ОТКЛЮЧЕН (нет модуля)"
        except ImportError:
            status = "неизвестно"
            
        ver = "v1.2.0 (Trafilatura)"
        toast(f"News App {ver}\nПарсер: {status}")
        print(f"App started. Version: {ver}, Trafilatura: {status}")
        
        # Initialize home screen data
        if hasattr(self.home_screen, '_fetch_and_build'):
             self.home_screen._fetch_and_build()

    def _go_to(self, screen_name: str) -> None:
        try:
            self.drawer.set_state("close")
        except Exception:
            pass
        self.screen_manager.current = screen_name

    def show_article(self, link: str) -> None:
        if not link:
            toast("Ссылка недоступна.")
            return
        
        payload = self.search_screen.article_payloads.get(link, {})
        
        # Check ONLY full-text cache (not snippet/description)
        cached_full_text = self.search_screen.article_cache.get(link)
        
        self.screen_manager.current = "article"
        # Сохраняем статью для факт-чекинга
        self.article_screen.current_article = payload
        
        if cached_full_text:
            # Use cached full text
            image_url = payload.get("image", "")
            self.article_screen.set_article_text(cached_full_text, image_url=image_url)
        else:
            # Always fetch and parse full article on first open
            self.article_screen.text_label.text = "Загружаю полную статью..."
            image_url = payload.get("image", "")
            if image_url:
                self.article_screen.image.source = image_url
                self.article_screen.image.height = "220dp"
                self.article_screen.image.opacity = 1
            threading.Thread(target=self._fetch_and_display, args=(link,), daemon=True).start()

    def _fetch_and_display(self, link: str) -> None:
        try:
            from news_search_core import fetch_article  # lazy import

            res = fetch_article(link) or {}
            text = (res.get("full_text") or "").strip()
            fetched_image = (res.get("image") or "").strip()
        except Exception as exc:
            err_msg = str(exc)
            Clock.schedule_once(lambda *_: toast(f"Ошибка: {err_msg}"), 0)
            return

        payload = self.search_screen.article_payloads.get(link, {})
        text = text or payload.get("full_text") or payload.get("description") or "Текст не извлечён."
        image_url = (payload.get("image", "") or "").strip() or fetched_image
        # Обновляем full_text в payload для факт-чекинга
        payload["full_text"] = text
        if image_url:
            payload["image"] = image_url
        self.search_screen.article_cache[link] = text
        Clock.schedule_once(lambda *_: self.article_screen.set_article_text(text, image_url=image_url), 0)

    def go_back(self) -> None:
        self.screen_manager.current = "search"


if __name__ == "__main__":
    NewsSearchApp().run()