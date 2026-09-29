# Парсер проектов Kwork.ru с БД и Telegram

## MCP-ассистент откликов (безопасный режим)

`kwork_responder_mcp.py` добавляет FastMCP-инструменты на Playwright. Он получает проекты
из отрендеренной страницы Kwork, оставляет только совпадающие с `KWORK_SPHERES`,
исключает `KWORK_EXCLUDE` и проекты, где уже больше 5 предложений, генерирует индивидуальный текст из шаблона и пишет
журнал в SQLite. Повторная обработка одного `project_id` невозможна благодаря
первичному ключу в таблице `responses`.

Скопируйте `.env.example` в `.env`, заполните значения и установите зависимости:

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 kwork_responder_mcp.py
```

Для авторизованных страниц укажите `KWORK_STORAGE_STATE` (Playwright storage
state JSON) или `KWORK_COOKIES_FILE` (экспорт Cookie-Editor). Пароли не нужно
хранить в коде или передавать MCP-инструментам.

Если state/cookies не указаны, MCP может выполнить обычный вход по
`KWORK_LOGIN` и `KWORK_PASSWORD` из локального `.env`. CAPTCHA и двухфакторную
проверку он не обходит: установите `KWORK_HEADLESS=0`, завершите проверку в
окне браузера и затем сохраните storage state для последующих запусков.

Инструмент `scan_projects` возвращает черновики. Цена берётся только из полей
проекта Kwork; `sent_price` равна минимальному найденному значению диапазона
(например, `5 000–20 000` → `5000`). При отсутствии цены черновик не должен
отправляться и требует ручной проверки. Инструмент `response_log` показывает
все поля журнала: ID, название, сферу, MIN/MAX, выбранную цену, текст, дату и
статус.

Автоматическая отправка через неофициальную браузерную автоматизацию намеренно
не включена: найденные на GitHub клиенты используют Playwright/cookies и
нестабильные внутренние формы Kwork, а публичного документированного API
отправки предложений не обнаружено. Подключать отправку следует отдельным
адаптером только после проверки разрешений Kwork; до этого статус всегда
`draft`.

# 📦 Установка

### 1. Установите зависимости:

```bash
pip3 install requests beautifulsoup4
```

### 2. Настройте Telegram бота:

#### Вариант A: Быстрая настройка

```bash
# 1. Создайте бота через @BotFather в Telegram
# 2. Узнайте свой Chat ID через @userinfobot
# 3. Отредактируйте config.py
nano config.py

# 4. Проверьте настройки
python3 test_telegram.py
```

#### Вариант B: Подробная инструкция

Откройте файл **TELEGRAM_SETUP.md** - там пошаговая инструкция с картинками и примерами

### 3. Файлы проекта:

```
kwork_parser_telegram.py  - основной парсер с Telegram
database_manager.py       - менеджер базы данных
telegram_bot.py           - класс для работы с Telegram API
config.py                 - настройки (РЕДАКТИРУЙТЕ ЭТОТ ФАЙЛ!)
test_telegram.py          - проверка настроек Telegram
TELEGRAM_SETUP.md         - подробная инструкция по настройке
```

## 🎯 Быстрый старт

```bash
# 1. Установка
pip3 install requests beautifulsoup4

# 2. Настройка Telegram (см. TELEGRAM_SETUP.md)
nano config.py  # Укажите токен и Chat ID

# 3. Проверка
python3 test_telegram.py

# 4. Запуск!
python3 kwork_parser_telegram.py
```

## ⚙️ Настройка config.py

```python
# Обязательные параметры
TELEGRAM_BOT_TOKEN = "1234567890:ABCdef..."  # От @BotFather
TELEGRAM_CHAT_ID = "123456789"               # От @userinfobot

# Опциональные параметры
PROJECTS_PER_MESSAGE = 5          # Проектов в одном сообщении (1-10)
SEND_INDIVIDUAL_PROJECTS = False  # True = каждый проект отдельно
SEND_STATISTICS = True            # Отправлять статистику
SEND_START_NOTIFICATION = True    # Уведомление о начале парсинга
```

## 🔄 Логика работы

```
1. Подключение к БД (kwork_projects.db)
   ↓
2. Отправка уведомления в Telegram о начале
   ↓
3. Парсинг страниц kwork.ru
   ↓
4. Извлечение данных из window.stateData
   ↓
5. Для каждого проекта:
   • Проверка ID в БД
   • Если есть → SKIP
   • Если нет → INSERT + отправка в Telegram
   ↓
6. Сохранение новых проектов в new_projects.json
   ↓
7. Отправка статистики в Telegram
```

## 📱 Примеры сообщений в Telegram

### При запуске:

```
🚀 Начало парсинга Kwork.ru

📄 Страницы: 1 - 3
⏱ Задержка: 2 сек
```

### Новый проект:

```
🆕 Разработка мобильного приложения

💰 Бюджет: 50000 - 100000 ₽
⏰ Осталось: 5 дней

👤 Заказчик: ivanov_tech
   📊 Проектов: 15 | Нанято: 87%

📝 Описание:
Требуется разработать...

🔗 Перейти к проекту
```

### Статистика:

```
📊 СТАТИСТИКА ПАРСИНГА

🔍 Всего спарсено: 45
✨ Новых проектов: 12
⊘ Пропущено (дубли): 33
💾 Всего в БД: 157
👥 Заказчиков в БД: 89
```

## 🛠️ Использование

### Базовый запуск:

```bash
python3 kwork_parser_telegram.py
```

### Программное использование:

```python
from kwork_parser_telegram import KworkParser

# С Telegram
parser = KworkParser("kwork.db", use_telegram=True)
stats = parser.parse_and_save(start_page=1, end_page=10, delay=3.0)

# Без Telegram
parser = KworkParser("kwork.db", use_telegram=False)
stats = parser.parse_and_save(start_page=1, end_page=5, delay=2.0)
```

## 📊 Структура базы данных

### Таблица `projects`:

* id, name, url, description
* price_limit, possible_price_limit
* category_id, status, time_left
* offers_count, dates, flags
* created_at, updated_at

### Таблица `buyers`:

* user_id, username, profile_url
* avatar, wants_count, hired_percent
* created_at, updated_at

### Таблица `project_buyers`:

* Связь many-to-many между проектами и заказчиками

## 🗄️ Команды SQLite

```bash
# Открыть БД
sqlite3 kwork_projects.db

# Статистика
SELECT COUNT(*) FROM projects;
SELECT COUNT(*) FROM buyers;

# Последние проекты
SELECT id, name, created_at FROM projects 
ORDER BY created_at DESC LIMIT 10;

# Поиск по бюджету
SELECT name, price_limit FROM projects 
WHERE CAST(price_limit AS INTEGER) > 50000;

# Выйти
.quit
```

## 🔧 Решение проблем

### ❌ "Укажите TELEGRAM_BOT_TOKEN в config.py"

**Решение:** Откройте config.py и замените `YOUR_BOT_TOKEN_HERE` на токен от @BotFather

### ❌ "Не удалось подключиться к Telegram боту"

**Решение:**

1. Проверьте правильность токена
2. Попробуйте: `python3 test_telegram.py`
3. Откройте в браузере: `https://api.telegram.org/bot<TOKEN>/getMe`

### ❌ "Ошибка отправки сообщения"

**Решение:**

1. Напишите боту `/start` (для личных сообщений)
2. Добавьте бота в группу как администратора (для групп)
3. Проверьте Chat ID через @userinfobot

### ⚠️ Бот молчит

**Проверьте:**

1. Есть ли новые проекты? (если все дубликаты - нечего отправлять)
2. Включены ли уведомления в config.py?
3. Правильно ли указан Chat ID?

### 🔍 Отладка

```bash
# Проверка настроек
python3 test_telegram.py

# Запуск с выводом логов
python3 kwork_parser_telegram.py 2>&1 | tee parser.log
```

## 📁 Выходные файлы

* `kwork_projects.db` - база данных SQLite
* `new_projects.json` - только новые проекты из последнего запуска
* `parser.log` - логи работы (опционально)

## 🔄 Автоматизация

### Cron (Linux/macOS):

```bash
# Редактировать crontab
crontab -e

# Запуск каждые 30 минут
*/30 * * * * cd /path/to/parser && python3 kwork_parser_telegram.py

# Запуск каждый час
0 * * * * cd /path/to/parser && python3 kwork_parser_telegram.py
```

### Task Scheduler (Windows):

1. Откройте "Планировщик заданий"
2. Создайте задачу
3. Укажите путь к Python и скрипту
4. Настройте расписание

## 🎨 Кастомизация

### Отдельное сообщение для каждого проекта:

```python
# config.py
SEND_INDIVIDUAL_PROJECTS = True
```

### Только статистика, без проектов:

```python
# kwork_parser_telegram.py
# Закомментируйте строку:
# self._send_to_telegram(new_projects)
```

### Свое форматирование сообщений:

Отредактируйте методы `_format_project_message` и `_format_projects_batch` в `telegram_bot.py`

## 📚 Дополнительные ресурсы

* **TELEGRAM_SETUP.md** - подробная инструкция по настройке Telegram
* **test_telegram.py** - скрипт проверки настроек
* **Telegram Bot API:** https://core.telegram.org/bots/api
* **@BotFather:** https://t.me/BotFather
* **@userinfobot:** https://t.me/userinfobot

## ✅ Чеклист перед запуском

* [ ] Установлены зависимости (`pip3 install requests beautifulsoup4`)
* [ ] Создан бот через @BotFather
* [ ] Получен токен бота
* [ ] Узнан Chat ID через @userinfobot
* [ ] Заполнен config.py (токен и Chat ID)
* [ ] Написано боту `/start` (если личный чат)
* [ ] Запущен `python3 test_telegram.py` - все ✅
* [ ] Запущен `python3 kwork_parser_telegram.py`
* [ ] Получены сообщения в Telegram 🎉
