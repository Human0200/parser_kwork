# Инструкция по запуску Kwork MCP

## Что делает сервис

`kwork_responder_mcp.py` запускает FastMCP-сервер и через Playwright:

- открывает страницу `https://kwork.ru/projects`;
- получает проекты из `window.stateData`;
- оставляет проекты только из заданных сфер;
- исключает проекты по стоп-словам;
- пропускает проекты, у которых больше 5 предложений;
- выбирает минимальную цену из диапазона проекта;
- создаёт индивидуальный текст отклика;
- сохраняет результаты и обработанные `project_id` в SQLite.

> Отправка выполняется только отдельным вызовом для одного сохранённого
> черновика и с параметром `confirm=True`.

## 1. Переход в каталог проекта

```bash
cd /Volumes/CUSU256DOC/programming/projects/parser_kwork
```

## 2. Установка

В проекте уже может существовать `.venv`. Если его нет, создайте:

```bash
python3 -m venv .venv
```

Установите зависимости и Chromium:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

## 3. Настройка `.env`

Файл `.env` находится в корне проекта и исключён из Git. Его нельзя
публиковать или отправлять другим людям.

Основные параметры:

```env
KWORK_LOGIN=ваш_логин
KWORK_PASSWORD=ваш_пароль

KWORK_SPHERES=Python,парсеры,Telegram-боты
KWORK_SKILLS=FastAPI,FastMCP,SQLite
KWORK_EXCLUDE=wordpress,tilda,joomla

KWORK_RESPONSE_TEMPLATE=Здравствуйте!\n\nМогу выполнить проект «{project_name}».\n{details}\n\nС уважением, {about}
KWORK_ABOUT=Ваше имя и краткое описание опыта
KWORK_DEFAULT_DEADLINE=

KWORK_DB=kwork_responses.db
KWORK_PROJECTS_URL=https://kwork.ru/projects?page={page}
KWORK_STORAGE_STATE=
KWORK_COOKIES_FILE=
KWORK_HEADLESS=0
```

Значения `KWORK_SPHERES`, `KWORK_SKILLS` и `KWORK_EXCLUDE` перечисляются через
запятую.

### Режим браузера

Для первого запуска используйте:

```env
KWORK_HEADLESS=0
```

Откроется окно Chromium. Если Kwork запросит CAPTCHA или двухфакторную
проверку, завершите её вручную. Сервис не обходит такие проверки.

После проверки работы можно включить фоновый режим:

```env
KWORK_HEADLESS=1
```

## 4. Проверка тестов

```bash
.venv/bin/python -m unittest -v test_kwork_responder.py
```

Нормальный результат:

```text
Ran 4 tests
OK
```

## 5. Запуск MCP-сервера

```bash
.venv/bin/python kwork_responder_mcp.py
```

Сервер использует транспорт stdio. После запуска он ожидает команды от
MCP-клиента, поэтому обычного меню в терминале не будет.

## 6. Подключение к MCP-клиенту

Добавьте сервер в конфигурацию клиента:

```json
{
  "mcpServers": {
    "kwork-responder": {
      "command": "/Volumes/CUSU256DOC/programming/projects/parser_kwork/.venv/bin/python",
      "args": [
        "/Volumes/CUSU256DOC/programming/projects/parser_kwork/kwork_responder_mcp.py"
      ]
    }
  }
}
```

Для Claude Code конфигурацию можно сохранить как `.mcp.json` в каталоге
проекта. После изменения конфигурации перезапустите MCP-клиент.

## 7. Доступные инструменты

### `scan_projects`

Получает одну страницу проектов и создаёт черновики:

```text
scan_projects(page=1)
```

Проект проходит, только если:

- найдено совпадение с `KWORK_SPHERES`;
- нет совпадения с `KWORK_EXCLUDE`;
- количество предложений известно и не превышает 5;
- этот `project_id` ещё не обрабатывался.

### `response_log`

Возвращает последние записи журнала:

```text
response_log(limit=50)
```

Журнал содержит:

- `project_id`;
- название проекта;
- определённую сферу;
- минимальную и максимальную цену;
- выбранную минимальную цену;
- текст отклика;
- дату;
- статус.

### `submit_response`

Заполняет и отправляет форму одного уже сохранённого черновика:

```text
submit_response(project_id="123456", confirm=True)
```

Перед вызовом проверьте нужный ID через `response_log`. Без `confirm=True`
сервис не выполняет отправку. После попытки статус в журнале меняется на
`submitted` или `failed`.

## 8. База данных

По умолчанию журнал хранится здесь:

```text
kwork_responses.db
```

Посмотреть последние записи:

```bash
sqlite3 kwork_responses.db \
  'SELECT project_id, name, min_price, max_price, sent_price, status FROM responses ORDER BY created_at DESC LIMIT 20;'
```

Поле `project_id` является первичным ключом. Поэтому повторная запись одного и
того же проекта невозможна.

## 9. Авторизация через cookies или storage state

Вместо логина и пароля можно использовать готовую браузерную сессию.

Playwright storage state:

```env
KWORK_STORAGE_STATE=/абсолютный/путь/kwork-state.json
KWORK_COOKIES_FILE=
```

Экспорт Cookie-Editor:

```env
KWORK_STORAGE_STATE=
KWORK_COOKIES_FILE=/абсолютный/путь/cookies.json
```

Если указан один из этих файлов, вход по `KWORK_LOGIN` и `KWORK_PASSWORD` не
выполняется.

## 10. Типичные ошибки

### `No module named playwright`

```bash
.venv/bin/python -m pip install -r requirements.txt
```

### Chromium не установлен

```bash
.venv/bin/python -m playwright install chromium
```

### `Kwork login did not complete`

Установите:

```env
KWORK_HEADLESS=0
```

Проверьте логин и пароль, затем завершите CAPTCHA или 2FA вручную.

### MCP-клиент не видит инструменты

Проверьте абсолютные пути в конфигурации MCP и перезапустите клиент. Сервер
должен запускаться именно Python-интерпретатором из `.venv`.

### Проекты не появляются повторно

Это ожидаемое поведение защиты от дублей. Уже обработанные ID находятся в
таблице `responses` файла `kwork_responses.db`.
