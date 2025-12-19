"""WebView компонент для отображения HTML статей в Kivy приложении."""

from kivy.utils import platform
from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock


class WebViewWidget(BoxLayout):
    """
    WebView виджет для отображения веб-страниц.
    - На Android использует нативный WebView
    - На десктопе можно использовать fallback (открытие в браузере)
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.webview = None
        self.url = None
        
        if platform == 'android':
            self._init_android_webview()
        else:
            # На десктопе создаём заглушку с сообщением
            from kivymd.uix.label import MDLabel
            self.add_widget(MDLabel(
                text="WebView доступен только на Android.\nОткройте статью в браузере через кнопку 🌐",
                halign="center",
                theme_text_color="Secondary"
            ))
    
    def _init_android_webview(self):
        """Инициализация Android WebView."""
        try:
            from jnius import autoclass, cast
            from android.runnable import run_on_ui_thread
            
            # Android классы
            WebView = autoclass('android.webkit.WebView')
            WebViewClient = autoclass('android.webkit.WebViewClient')
            LayoutParams = autoclass('android.view.ViewGroup$LayoutParams')
            LinearLayout = autoclass('android.widget.LinearLayout')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            
            # Получаем активность и layout
            activity = PythonActivity.mActivity
            
            @run_on_ui_thread
            def create_webview():
                # Создаём WebView
                self.webview = WebView(activity)
                self.webview.getSettings().setJavaScriptEnabled(True)
                self.webview.getSettings().setBuiltInZoomControls(True)
                self.webview.getSettings().setDisplayZoomControls(False)
                self.webview.setWebViewClient(WebViewClient())
                
                # Добавляем в layout
                layout = cast(LinearLayout, activity.findViewById(0x01020002))  # android.R.id.content
                layout.addView(self.webview, LayoutParams(
                    LayoutParams.MATCH_PARENT,
                    LayoutParams.MATCH_PARENT
                ))
            
            create_webview()
            
        except Exception as e:
            print(f"[WebView] Ошибка инициализации: {e}")
            from kivymd.uix.label import MDLabel
            self.add_widget(MDLabel(
                text=f"Ошибка WebView: {str(e)[:50]}",
                halign="center"
            ))
    
    def load_url(self, url: str):
        """Загрузить URL в WebView."""
        self.url = url
        
        if platform == 'android' and self.webview:
            try:
                from android.runnable import run_on_ui_thread
                
                @run_on_ui_thread
                def load():
                    self.webview.loadUrl(url)
                
                load()
            except Exception as e:
                print(f"[WebView] Ошибка загрузки URL: {e}")
        else:
            # На десктопе открываем в браузере
            import webbrowser
            webbrowser.open(url)
    
    def load_html(self, html: str, base_url: str = ""):
        """Загрузить HTML контент напрямую."""
        if platform == 'android' and self.webview:
            try:
                from android.runnable import run_on_ui_thread
                
                @run_on_ui_thread
                def load():
                    self.webview.loadDataWithBaseURL(
                        base_url or "about:blank",
                        html,
                        "text/html",
                        "UTF-8",
                        None
                    )
                
                load()
            except Exception as e:
                print(f"[WebView] Ошибка загрузки HTML: {e}")
    
    def go_back(self):
        """Вернуться на предыдущую страницу."""
        if platform == 'android' and self.webview:
            try:
                from android.runnable import run_on_ui_thread
                
                @run_on_ui_thread
                def back():
                    if self.webview.canGoBack():
                        self.webview.goBack()
                
                back()
            except Exception as e:
                print(f"[WebView] Ошибка go_back: {e}")
    
    def can_go_back(self):
        """Проверить, можно ли вернуться назад."""
        if platform == 'android' and self.webview:
            try:
                return self.webview.canGoBack()
            except:
                return False
        return False
    
    def destroy(self):
        """Очистить WebView."""
        if platform == 'android' and self.webview:
            try:
                from android.runnable import run_on_ui_thread
                from jnius import autoclass, cast
                
                @run_on_ui_thread
                def cleanup():
                    PythonActivity = autoclass('org.kivy.android.PythonActivity')
                    LinearLayout = autoclass('android.widget.LinearLayout')
                    activity = PythonActivity.mActivity
                    layout = cast(LinearLayout, activity.findViewById(0x01020002))
                    layout.removeView(self.webview)
                    self.webview.destroy()
                
                cleanup()
            except Exception as e:
                print(f"[WebView] Ошибка destroy: {e}")


# Тестовая функция
def test_webview():
    """Тест WebView компонента."""
    from kivy.app import App
    from kivymd.app import MDApp
    
    class TestApp(MDApp):
        def build(self):
            webview = WebViewWidget()
            Clock.schedule_once(lambda dt: webview.load_url("https://news.google.com"), 1)
            return webview
    
    TestApp().run()


if __name__ == "__main__":
    test_webview()
