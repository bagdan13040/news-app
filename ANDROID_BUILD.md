# Инструкция по сборке Android APK для NewsSearch

## Предварительные требования

### На Windows (через WSL2)

1. **Установите WSL2:**
```powershell
wsl --install -d Ubuntu-22.04
```

2. **Войдите в WSL:**
```powershell
wsl
```

3. **Обновите систему:**
```bash
sudo apt update && sudo apt upgrade -y
```

4. **Установите необходимые пакеты:**
```bash
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool pkg-config \
    zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev \
    ccache libgdbm-dev libsqlite3-dev libreadline-dev libbz2-dev
```

### На Linux (нативно)

Выполните шаг 4 из инструкции выше.

### На macOS

```bash
brew install python3
brew install autoconf automake libtool pkg-config
```

## Установка Buildozer

```bash
pip3 install --upgrade buildozer
pip3 install --upgrade cython
```

## Подготовка проекта

1. **Скопируйте проект в WSL (если используете Windows):**
```bash
# В WSL создайте директорию
mkdir -p ~/projects
cd ~/projects

# Скопируйте файлы из Windows (замените путь)
cp -r /mnt/e/news_final ./
cd news_final
```

2. **Проверьте buildozer.spec:**
Файл `buildozer.spec` уже создан и настроен для вашего проекта.

## Сборка APK

### Первая сборка (займет 30-60 минут)

```bash
# Очистка предыдущих сборок (если есть)
buildozer android clean

# Сборка debug версии
buildozer -v android debug
```

Флаг `-v` включает подробный вывод для отладки.

### Последующие сборки (5-10 минут)

```bash
buildozer android debug
```

### Сборка release версии (для публикации)

```bash
buildozer android release
```

## Что происходит при сборке

1. **Скачивание SDK/NDK** - Android SDK, NDK автоматически загружаются
2. **Установка зависимостей** - Python пакеты компилируются для ARM
3. **Компиляция** - Ваш код упаковывается в APK
4. **Подпись** - APK подписывается (debug ключом для debug версии)

## Расположение APK

После успешной сборки APK будет в:
```
bin/newssearch-1.0.0-arm64-v8a_armeabi-v7a-debug.apk
```

## Установка на устройство

### Через USB:

```bash
# Включите "Отладку по USB" на Android устройстве
# Подключите устройство

# Установка
buildozer android deploy run
```

### Вручную:

1. Скопируйте APK на устройство
2. Откройте файл на Android
3. Разрешите установку из неизвестных источников

## Отладка

### Просмотр логов приложения:

```bash
buildozer android logcat
```

### Фильтр только Python логов:

```bash
adb logcat -s python:D
```

## Решение проблем

### Ошибка "Command failed"

```bash
# Очистите кеш и пересоберите
buildozer android clean
rm -rf .buildozer
buildozer -v android debug
```

### Ошибка с зависимостями

Проверьте `requirements` в `buildozer.spec`. Некоторые пакеты могут требовать recipes для python-for-android.

### Недостаточно памяти

```bash
# Увеличьте swap (Linux/WSL)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Проблемы с lxml/trafilatura

Если возникают ошибки с `lxml` или `trafilatura`, можно:
1. Использовать альтернативный парсер
2. Или собрать recipe для lxml вручную

## Размер APK

Первая сборка создаст APK размером 30-50 МБ из-за:
- Python runtime (~15 МБ)
- Kivy/KivyMD (~10 МБ)
- Ваши зависимости (~10-15 МБ)

## Оптимизация

### Уменьшение размера:

В `buildozer.spec` измените:
```ini
# Собирайте только для одной архитектуры
android.archs = arm64-v8a
```

### Использование ProGuard (минификация):

```ini
android.add_gradle_repositories = maven { url 'https://maven.google.com' }
```

## Публикация в Google Play

1. **Создайте release ключ:**
```bash
keytool -genkey -v -keystore newsearch-release.keystore -alias newsearch \
        -keyalg RSA -keysize 2048 -validity 10000
```

2. **Настройте подпись в buildozer.spec:**
```ini
[app]
# ... другие настройки ...

# Путь к keystore
android.release_keystore = newsearch-release.keystore
android.release_keystore_passwd = ваш_пароль
android.release_keyalg = SHA1withRSA
```

3. **Соберите release:**
```bash
buildozer android release
```

4. **Загрузите в Google Play Console**

## Автоматизация через GitHub Actions

Создайте `.github/workflows/build-android.yml` для автоматической сборки при коммитах.

## Полезные команды

```bash
# Список подключенных устройств
adb devices

# Просмотр установленных пакетов на устройстве
adb shell pm list packages | grep newssearch

# Удаление приложения
adb uninstall org.newssearch.newssearch

# Проверка версии buildozer
buildozer --version

# Список доступных target платформ
buildozer --help
```

## Поддержка

- **Buildozer документация:** https://buildozer.readthedocs.io/
- **Python-for-Android:** https://python-for-android.readthedocs.io/
- **Kivy:** https://kivy.org/doc/stable/

## Важные замечания

1. **API ключи** - Убедитесь, что `.env` файл включен в APK или используйте environment variables
2. **Разрешения** - Приложение запрашивает INTERNET, ACCESS_NETWORK_STATE, READ/WRITE_EXTERNAL_STORAGE
3. **Тестирование** - Всегда тестируйте на реальном устройстве перед публикацией
4. **Обновления** - При изменении версии обновите `version` в buildozer.spec

---

**Примерное время первой сборки:**
- Скачивание зависимостей: 10-15 минут
- Компиляция: 20-30 минут
- Упаковка APK: 5 минут
**Итого: 35-50 минут**

**Последующие сборки:** 5-10 минут
