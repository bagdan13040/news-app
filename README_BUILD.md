# Инструкция по сборке APK (Android)

Так как вы используете Windows, самый простой способ собрать APK — использовать **Google Colab**. Это бесплатно и не требует установки Linux на ваш компьютер.

## Шаг 1: Подготовка файлов
Убедитесь, что у вас есть все файлы проекта в одной папке (как сейчас).
Важно: файл `.env` с ключами API также должен быть загружен, чтобы приложение работало.

## Шаг 2: Откройте Google Colab
1. Перейдите на [Google Colab](https://colab.research.google.com/).
2. Создайте новый блокнот (New Notebook).

## Шаг 3: Команды для сборки
Скопируйте следующий код в ячейку блокнота и запустите её.
Вам нужно будет загрузить архив с вашим проектом в Colab.

```python
# 1. Установка зависимостей Buildozer
!sudo apt-get update
!sudo apt-get install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev
!pip3 install --upgrade buildozer cython virtualenv

# 2. Загрузка проекта
# Сначала заархивируйте вашу папку news_final в news_final.zip на компьютере.
# Затем загрузите news_final.zip в файлы Colab (значок папки слева -> значок загрузки).

import os
if not os.path.exists("news_final"):
    !unzip news_final.zip -d .

# 3. Переход в папку проекта
%cd news_final

# 4. Запуск сборки (это займет 15-30 минут)
# На вопрос "Do you want to accept the license?" (y/n) нужно будет ответить y, 
# но buildozer.spec уже настроен на автоматическое принятие (android.accept_sdk_license = True).
!buildozer android debug
```

## Шаг 4: Скачивание APK
После успешной сборки файл `.apk` появится в папке `bin/` внутри `news_final`.
Скачайте его на телефон и установите.

## Примечания
- **OpenAI Key**: Убедитесь, что ваш ключ API находится в файле `.env` или прописан в коде, иначе функции ИИ не будут работать.
- **Первый запуск**: Сборка в первый раз занимает много времени, так как скачивается Android SDK/NDK.
