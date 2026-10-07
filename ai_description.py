"""Генерация текста отклика через RouterAI (OpenAI-compatible API)."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

DEFAULT_BASE_URL = "https://routerai.ru/api/v1"
DEFAULT_MODEL = "mistralai/mistral-nemo"
# Запасные модели, если основная недоступна / rate-limit / пустой ответ
FALLBACK_MODELS = (
    "mistralai/mistral-nemo",
    "meta-llama/llama-3.1-8b-instruct",
    "deepseek/deepseek-v4-flash",
    "z-ai/glm-5.3-flash",
)
DESC_MIN = 150
DESC_MAX = 2000
DEFAULT_SIGNATURE = "Команда FlowTeam"
DEFAULT_CASES_FILE = "portfolio_cases.json"

# Темы задачи → ключевые слова в названии/ТЗ заказчика
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "image_gen": (
        "изображен", "картин", "фото", "нейросет", "stable diffusion", "midjourney",
        "генерац", "дизайн по", "watermark", "водян", "отделк", "рендер",
    ),
    "parser": (
        "парсер", "parser", "скрап", "парсинг", "сбор данных", "мониторинг канал",
        "закрыт", "подписк",
    ),
    "telegram": ("telegram", "телеграм", "тг-бот", "tg бот", "тг бот"),
    "vk": ("вконтакте", "вк-бот", "vk бот", " vk", "вк ", "сообществ"),
    "max": (" max", "макс-бот", "max.ru", "max бот"),
    "shop": (
        "магазин", "каталог", "товар", "e-commerce", "интернет-магазин", "корзин",
        "оплат", "оптов", "инструмент", "продаж",
    ),
    "crm": ("crm", "срм", "лид", "воронк", "клиентск"),
    "voice": ("озвуч", "голос", "speech", "tts", "text to speech"),
    "geo": ("гео", "трекер", "локац", "gps", "карт"),
    "analytics": ("интерес", "анализ сообщ", "nlp", "классификац"),
    "website": (
        "сайт", "лендинг", "wordpress", "bitrix", "битрикс", "landing", "веб",
        "frontend", "фронт",
    ),
    "bot_generic": ("бот", "chat-bot", "чат-бот", "чатбот", "мини.?апп", "mini.?app"),
    "logistics": ("перевоз", "груз", "логистик", "доставк", "заказ на перевоз"),
}

_LINK_RE = re.compile(
    r"https?://[^\s<>\"')\]]+|@[A-Za-z][A-Za-z0-9_]{3,}",
    re.I,
)

_CASE_SECTIONS = (
    ("websites", "Сайты"),
    ("telegram_bots", "Telegram-боты"),
    ("max_bots", "MAX-боты"),
    ("parsers", "Парсеры"),
    ("flagship_projects", "Крупные проекты"),
)


def _team_signature() -> str:
    return (
        os.getenv("KWORK_SIGNATURE", "").strip()
        or os.getenv("KWORK_ABOUT", "").strip()
        or DEFAULT_SIGNATURE
    )


def _template_for_prompt(template: str) -> str:
    """Шаблон без финальной подписи — подпись добавим сами."""
    text = (template or "").strip()
    text = re.sub(
        r"\n*\s*С уважением\s*,?\s*\n+\s*.+\s*$",
        "",
        text,
        flags=re.I,
    )
    return text.strip()


def _cases_path() -> Path:
    raw = os.getenv("KWORK_CASES_FILE", DEFAULT_CASES_FILE).strip() or DEFAULT_CASES_FILE
    path = Path(raw)
    if not path.is_file():
        path = Path(__file__).resolve().parent / raw
    return path


@lru_cache(maxsize=1)
def _load_portfolio_raw() -> dict[str, Any]:
    path = _cases_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_portfolio_cases() -> list[dict[str, Any]]:
    """Плоский список кейсов из portfolio_cases.json."""
    data = _load_portfolio_raw()
    out: list[dict[str, Any]] = []
    for key, _label in _CASE_SECTIONS:
        items = data.get(key) or []
        if not isinstance(items, list):
            continue
        for raw in items:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            name = str(raw.get("name") or "").strip()
            desc = str(raw.get("description") or "").strip()
            topics = [str(t).strip() for t in (raw.get("topics") or []) if str(t).strip()]
            if not url and not name:
                continue
            out.append(
                {
                    "url": url,
                    "name": name,
                    "description": desc,
                    "topics": topics,
                    "section": key,
                    "ref": url or name,
                }
            )
    return out


def format_portfolio_for_prompt() -> str:
    """Текстовый блок кейсов для промпта."""
    data = _load_portfolio_raw()
    if not data:
        return ""
    lines: list[str] = []
    for key, label in _CASE_SECTIONS:
        items = data.get(key) or []
        if not isinstance(items, list) or not items:
            continue
        lines.append(f"{label}:")
        for raw in items:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            name = str(raw.get("name") or "").strip()
            desc = str(raw.get("description") or "").strip()
            if url and desc:
                lines.append(f"{url} — {desc}")
            elif url:
                lines.append(url)
            elif name and desc:
                lines.append(f"{name} — {desc}")
            elif name:
                lines.append(name)
        lines.append("")
    return "\n".join(lines).strip()


def _extract_examples_annotated(
    template: str = "", *, limit: int = 40
) -> list[tuple[str, str]]:
    """[(ссылка/@бот или имя проекта, описание), …] из JSON кейсов."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for case in load_portfolio_cases():
        ref = case["ref"]
        key = ref.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((ref, case.get("description") or ""))
        if len(out) >= limit:
            break
    if not out and template:
        for m in _LINK_RE.finditer(template):
            item = m.group(0).rstrip(".,);]")
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((item, ""))
            if len(out) >= limit:
                break
    return out


def _extract_examples(template: str = "", *, limit: int = 40) -> list[str]:
    return [item for item, _ in _extract_examples_annotated(template, limit=limit)]


def _detect_topics(blob: str) -> dict[str, int]:
    """Веса тем по тексту задачи (название + описание заказчика)."""
    text = f" {blob.lower()} "
    weights: dict[str, int] = {}
    for topic, keys in _TOPIC_KEYWORDS.items():
        hits = sum(1 for k in keys if k in text)
        if hits:
            base = 3 if topic in ("image_gen", "parser", "crm", "shop") else 2
            if topic == "bot_generic":
                base = 1
            weights[topic] = base + hits
    return weights


def _overlap_bonus(item: str, comment: str, blob: str) -> int:
    """Бонус, если слова из описания кейса встречаются в ТЗ."""
    text = blob.lower()
    parts = f"{item} {comment}".lower()
    words = [w for w in re.split(r"[^\wа-яё]+", parts) if len(w) >= 4]
    if not words:
        return 0
    stop = {
        "сайт", "https", "http", "бота", "боты", "проект", "онлайн", "услуг",
        "разработк", "продаже", "продажа", "продаж", "max",
    }
    hits = sum(1 for w in words if w not in stop and w in text)
    return min(10, hits * 3)


def _score_case(case: dict[str, Any], topics: dict[str, int], blob: str) -> int:
    """Скор кейса по пересечению topics из JSON с темами задачи."""
    case_topics = set(case.get("topics") or [])
    if not case_topics and not topics:
        return 0

    narrow = {t for t in topics if t not in ("bot_generic", "website")}
    score = 0
    for topic, tw in topics.items():
        if topic not in case_topics:
            continue
        if topic in ("bot_generic", "website") and narrow:
            if not (case_topics & narrow):
                continue
            score += min(tw, 1)
        else:
            score += tw * 3

    ref = str(case.get("ref") or "")
    desc = str(case.get("description") or "")
    score += _overlap_bonus(ref, desc, blob)

    section = case.get("section") or ""
    if section == "websites" and any(
        t in topics for t in ("image_gen", "parser", "telegram", "vk", "max")
    ):
        if "shop" not in topics and "website" not in topics and "crm" not in topics:
            score -= 6

    return score


def _pick_examples_for_project(
    name: str,
    client_text: str,
    examples: list[str] | None = None,
    *,
    n: int = 3,
    annotated: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Выбирает релевантные кейсы по теме задачи."""
    cases = load_portfolio_cases()
    if not cases:
        if annotated is None:
            annotated = [(ex, "") for ex in (examples or [])]
        return [item for item, _ in (annotated or [])][:n]

    blob = f"{name} {client_text}"
    topics = _detect_topics(blob)
    scored: list[tuple[int, str]] = []
    for case in cases:
        ref = str(case.get("url") or "").strip()
        if not ref:
            continue
        score = _score_case(case, topics, blob)
        scored.append((score, ref))

    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    return [ref for s, ref in scored if s > 0][:n]


def _format_example_hints(
    picked: list[str], annotated: list[tuple[str, str]]
) -> list[str]:
    """Строки для промпта: ссылка + описание кейса."""
    cmap = {item: comment for item, comment in annotated}
    for case in load_portfolio_cases():
        ref = case.get("url") or case.get("name") or ""
        if ref and ref not in cmap:
            cmap[ref] = case.get("description") or ""
    lines: list[str] = []
    for item in picked:
        comment = cmap.get(item, "")
        lines.append(f"{item} — {comment}" if comment else item)
    return lines


def _allowed_example_refs() -> set[str]:
    """Разрешённые ссылки/@боты из портфолио (нормализованные)."""
    allowed: set[str] = set()
    for case in load_portfolio_cases():
        url = str(case.get("url") or "").strip().rstrip("/")
        if url:
            allowed.add(url.lower())
            # без trailing slash и с ним
            allowed.add((url + "/").lower())
    return allowed


def _normalize_ref(ref: str) -> str:
    ref = (ref or "").strip().rstrip(".,);]")
    if ref.startswith("http"):
        return ref.rstrip("/").lower()
    return ref.lower()


def _is_allowed_ref(ref: str, allowed: set[str]) -> bool:
    n = _normalize_ref(ref)
    if n in allowed:
        return True
    # @боты — точное совпадение без учёта регистра
    if n.startswith("@"):
        return n in allowed
    # http: достаточно совпадения хоста+пути из портфолио
    for a in allowed:
        if not a.startswith("http"):
            continue
        if n == a or n.startswith(a + "/") or a.startswith(n):
            return True
    return False


def _sanitize_example_links(text: str, examples: list[str]) -> str:
    """Меняет выдуманные ссылки на реальные из портфолио; если нет — дописывает блок."""
    allowed = _allowed_example_refs()
    if not allowed and not examples:
        return text

    replacements = [
        ex for ex in examples if _is_allowed_ref(ex, allowed) or not allowed
    ]
    if not replacements:
        replacements = [c["url"] for c in load_portfolio_cases() if c.get("url")][:3]

    found = list(_LINK_RE.finditer(text or ""))
    if not found:
        # нет ссылок вообще — дописать блок
        if not replacements:
            return text
        cmap = {
            str(c.get("url") or ""): str(c.get("description") or "")
            for c in load_portfolio_cases()
        }
        lines = []
        for ex in replacements[:3]:
            desc = cmap.get(ex) or cmap.get(ex.rstrip("/")) or ""
            lines.append(f"{ex} — {desc}" if desc else ex)
        return f"{text.rstrip()}\n\nПримеры наших работ:\n" + "\n".join(lines)

    # Заменяем фейки на реальные по кругу; реальные оставляем
    out: list[str] = []
    last = 0
    repl_i = 0
    used_real: list[str] = []
    for m in found:
        raw = m.group(0).rstrip(".,);]")
        out.append(text[last : m.start()])
        if _is_allowed_ref(raw, allowed):
            out.append(raw)
            used_real.append(raw)
        else:
            if replacements:
                sub = replacements[repl_i % len(replacements)]
                repl_i += 1
                out.append(sub)
                used_real.append(sub)
            # иначе просто вырезаем фейк
        last = m.end()
    out.append(text[last:])
    text = "".join(out)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([.,;:])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # если после всего реальных всё равно нет — блок в конце
    still_real = [
        m.group(0).rstrip(".,);]")
        for m in _LINK_RE.finditer(text)
        if _is_allowed_ref(m.group(0), allowed)
    ]
    if still_real or not replacements:
        return text
    return (
        f"{text.rstrip()}\n\nПримеры наших работ:\n"
        + "\n".join(replacements[:3])
    )


def _ensure_example_links(text: str, examples: list[str]) -> str:
    """Совместимость: санитизация + гарантия реальных примеров."""
    return _sanitize_example_links(text, examples)


_BAD_OPENING_MARKERS = re.compile(
    r"приветствуем\s+вас|"
    r"поняли,\s*что\s+вам|"
    r"видим,\s*что\s+вам\s+нуж|"
    r"вам\s+нужен\s+специалист|"
    r"вам\s+необходим",
    re.I,
)

_GOOD_OPENING_START = re.compile(r"ознакомил|изучил|по\s+описанию", re.I)


def _fix_opening(text: str) -> str:
    """Заменяет шаблонное «поняли, что вам нужен специалист…» на нормальный заход."""
    body = (text or "").strip()
    m = re.match(r"^(?:здравствуйте|приветствуем\s+вас)[!.,]?\s*", body, flags=re.I)
    rest = body[m.end() :].lstrip() if m else body

    if _GOOD_OPENING_START.match(rest):
        return f"Здравствуйте!\n\n{rest}".strip()

    if not _BAD_OPENING_MARKERS.search(rest[:400]):
        return f"Здравствуйте!\n\n{rest}".strip()

    paras = re.split(r"\n\s*\n", rest, maxsplit=1)
    first = paras[0].strip()
    tail = paras[1].strip() if len(paras) > 1 else ""

    # Достаём суть из «Вам необходима X» / остатка после «поняли, что…»
    task_bits: list[str] = []
    m_need = re.search(
        r"вам\s+(?:необходим[аоыё]?|нужн[аоыё]?)\s+([^.!?]+)[.!?]?",
        first,
        flags=re.I,
    )
    if m_need:
        bit = m_need.group(1).strip()
        if bit and "специалист" not in bit.lower():
            task_bits.append(bit[0].upper() + bit[1:] if bit else bit)

    salvaged = first
    salvaged = re.sub(
        r"^(?:мы\s+)?(?:поняли|видим),\s*что\s+вам\s+[^.!?]*[.!?]\s*",
        "",
        salvaged,
        count=1,
        flags=re.I,
    )
    salvaged = re.sub(
        r"^вам\s+(?:необходим[аоыё]?|нужн[аоыё]?)\s+[^.!?]*[.!?]\s*",
        "",
        salvaged,
        count=1,
        flags=re.I,
    )
    salvaged = re.sub(
        r"^наша\s+команда\s+готова\s+[^.!?]*[.!?]\s*",
        "",
        salvaged,
        count=1,
        flags=re.I,
    ).strip()
    if salvaged and not _BAD_OPENING_MARKERS.search(salvaged[:200]):
        task_bits.append(salvaged)

    if task_bits:
        opening = "Ознакомились с описанием задачи: " + " ".join(task_bits)
        if not opening.rstrip().endswith((".", "!", "?")):
            opening += "."
    else:
        opening = "Ознакомились с описанием задачи и готовы взяться за её реализацию."

    parts = [opening]
    if tail:
        parts.append(tail)
    return "Здравствуйте!\n\n" + "\n\n".join(parts)


def _fix_signature(text: str) -> str:
    """Нормализует конец: одна подпись «С уважением, / Команда …»."""
    sig = _team_signature()
    patterns = (
        r"\[Ваше имя[^\]]*\]",
        r"\[название команды[^\]]*\]",
        r"\[имя[^\]]*\]",
        r"Ваше имя/название команды",
        r"Ваше имя и краткая информация",
    )
    for pat in patterns:
        text = re.sub(pat, sig, text, flags=re.I)

    # Срезаем любые варианты хвоста «С уважением…» и повторы имени
    text = re.sub(
        r"(?:\n+\s*С уважением\s*,?\s*(?:\n+\s*" + re.escape(sig) + r"\s*)+)+$",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(?:\n+\s*" + re.escape(sig) + r"\s*)+$",
        "",
        text,
        flags=re.I,
    )
    text = text.rstrip()
    return f"{text}\n\nС уважением,\n{sig}"


def _load_template() -> str:
    template_file = os.getenv("KWORK_RESPONSE_TEMPLATE_FILE", "response_template.txt")
    path = Path(template_file)
    if not path.is_file():
        path = Path(__file__).resolve().parent / template_file
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return (os.getenv("KWORK_RESPONSE_TEMPLATE") or "").replace("\\n", "\n").strip()


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_message_text(data: dict[str, Any]) -> str:
    """Достаёт текст из OpenAI-compatible ответа (в т.ч. reasoning-моделей)."""
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    # иногда content — список частей
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
        joined = "".join(parts).strip()
        if joined:
            return joined
    # fallback поля
    for key in ("text", "reasoning", "reasoning_content"):
        val = msg.get(key) or choices[0].get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _call_chat(model: str, system: str, user: str, *, api_key: str, base_url: str, timeout: int) -> dict[str, Any]:
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.35,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    # RouterAI иногда отдаёт HTTP 200 с {"error": {...}}
    try:
        data = resp.json()
    except Exception:
        return {
            "ok": False,
            "error": f"RouterAI HTTP {resp.status_code}: не JSON ({resp.text[:200]})",
            "model": model,
        }

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            msg = err.get("message") or str(err)
            code = err.get("code", "")
        else:
            msg = str(err)
            code = ""
        return {"ok": False, "error": f"RouterAI {code}: {msg}".strip(), "model": model, "retryable": True}

    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"RouterAI HTTP {resp.status_code}: {data}",
            "model": model,
            "retryable": resp.status_code in {408, 429, 500, 502, 503, 504},
        }

    text = _extract_message_text(data)
    if text.startswith("```"):
        text = re.sub(r"^```(?:\w+)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    text = text.strip().strip('"').strip()
    if not text:
        return {"ok": False, "error": "пустой ответ модели", "model": model, "retryable": True}
    return {"ok": True, "text": text, "model": model}


def generate_offer_description(
    project_name: str,
    client_description: str = "",
    *,
    price: int | None = None,
    days: int | None = None,
    timeout: int = 90,
) -> dict[str, Any]:
    """Возвращает {ok, text} или {ok: False, error}."""
    api_key = os.getenv("ROUTERAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "Нет ROUTERAI_API_KEY в .env"}

    base_url = os.getenv("ROUTERAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    primary = os.getenv("AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    models: list[str] = []
    for m in (primary, *FALLBACK_MODELS):
        if m and m not in models:
            models.append(m)

    template = _template_for_prompt(_load_template())
    client_text = _strip_html(client_description)[:6000]
    name = (project_name or "Проект").strip()
    signature = _team_signature()

    price_line = ""
    if price and int(price) > 0:
        price_line += f"Цена отклика (уже выбрана, не менять и не дробить): {int(price)} ₽.\n"
    if days and int(days) > 0:
        price_line += f"Срок отклика (уже выбран, не менять): {int(days)} дн.\n"

    portfolio_block = format_portfolio_for_prompt()
    materials = template
    if portfolio_block:
        materials = f"{template}\n\n=== КЕЙСЫ / ПРИМЕРЫ РАБОТ ===\n{portfolio_block}".strip()

    annotated = _extract_examples_annotated(template)
    examples_pool = [item for item, _ in annotated] or _extract_examples(template)
    suggested = _pick_examples_for_project(
        name, client_text, examples_pool, n=3, annotated=annotated
    )
    hint_lines = _format_example_hints(suggested or examples_pool[:5], annotated)
    examples_hint = (
        "Обязательно вставь в текст 2–3 ПРИМЕРА БЛИЗКИХ ПО ТЕМЕ "
        "(ссылки/@боты ТОЛЬКО из списка ниже — они уже отобраны под задачу):\n"
        + "\n".join(f"- {line}" for line in hint_lines)
        + "\nНе подставляй случайные сайты/ботов из портфолио вне этого списка."
        if hint_lines
        else "Если в материалах есть ссылки или @боты — обязательно вставь 2–3 штуки."
    )

    system = (
        "Ты пишешь короткий отклик на Kwork от лица команды разработчиков.\n"
        "Цель — показать, что ознакомились с задачей и готовы её сделать, а не писать "
        "коммерческое предложение с этапами и сметой.\n\n"
        "Структура ответа (строго):\n"
        "1) Короткое приветствие («Здравствуйте!» — достаточно) + 1–3 предложения:\n"
        "   начни с формулировки вроде «Ознакомились с описанием задачи…» / "
        "«Изучили ваше ТЗ…» / «По описанию проекта видим…», затем своими словами "
        "суть задачи (платформа, ключевые функции). Без пересказа всего ТЗ.\n"
        "   ЗАПРЕЩЕНО начинать с: «Приветствуем вас», «Мы поняли, что вам нужен "
        "специалист…», «Вам необходима…», перечисления вакансии/роли вместо сути проекта.\n"
        "2) 2–4 предложения: релевантный опыт + ОБЯЗАТЕЛЬНО 2–3 конкретных примера "
        "со ссылками https://… или @ботами ТОЛЬКО из предложенного списка "
        "(они подобраны по теме задачи). Не бери посторонние примеры из шаблона. "
        "Без примеров со ссылками отклик недействителен.\n"
        "3) 1–2 предложения: готовность взяться в указанные цену и срок; "
        "можно кратко упомянуть поддержку 14 дней, если она есть в материалах.\n\n"
        "Жёсткие запреты:\n"
        "- НЕ пиши пошаговый план («этап 1/2/3»), смету по блокам, разбивку цены.\n"
        "- НЕ указывай свои цены, сроки, «итого», $ или ₽ за API/генерации/хостинг.\n"
        "- НЕ выдумывай сервисы, модели ИИ, хостинг (DigitalOcean и т.п.), цены API, "
        "стек, кейсы и ссылки, которых нет в материалах о команде.\n"
        "- СТРОГО ЗАПРЕЩЕНО писать placeholder-ссылки: example.com, example2.com, "
        "your-site, site.ru, domain.com и любые URL/@боты не из списка примеров.\n"
        "- НЕ копируй шаблон целиком и не перечисляй все сайты/ботов подряд — "
        "только 2–3 релевантных примера со ссылками.\n"
        "- НЕ обещай то, чего заказчик не просил.\n\n"
        "Стиль: русский, деловой, живой, без воды и канцелярита. "
        "Без markdown (** ##), без заголовка «Отклик:». "
        "Целевая длина 500–1100 символов, максимум "
        f"{DESC_MAX} (минимум {DESC_MIN}).\n"
        "НЕ пиши подпись «С уважением» и имя команды — их добавим отдельно. "
        "Запрещены плейсхолдеры в квадратных скобках и фразы «Ваше имя». "
        "Верни только текст отклика."
    )
    user = (
        f"Название проекта: {name}\n"
        f"{price_line}"
        f"\n=== ОПИСАНИЕ ЗАКАЗЧИКА ===\n"
        f"{client_text or '(пусто — опирайся на название и релевантный опыт)'}\n\n"
        f"=== МАТЕРИАЛЫ О КОМАНДЕ (единственный источник фактов) ===\n"
        f"{materials or '(шаблон пуст)'}\n\n"
        f"{examples_hint}\n\n"
        f"Подпись команды (в текст НЕ включать): {signature}\n\n"
        "Напиши один короткий персонализированный отклик по правилам выше.\n"
        "Пример хорошего начала: «Здравствуйте! Ознакомились с описанием задачи: "
        "нужен … . Готовы реализовать …»\n"
        "Плохое начало (не использовать): «Приветствуем вас! Мы поняли, что вам нужен "
        "специалист по …»"
    )

    errors: list[str] = []
    for model in models:
        try:
            result = _call_chat(model, system, user, api_key=api_key, base_url=base_url, timeout=timeout)
        except requests.RequestException as exc:
            errors.append(f"{model}: сеть {exc}")
            continue
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            continue

        if not result.get("ok"):
            errors.append(f"{model}: {result.get('error')}")
            continue

        text = str(result["text"])
        if len(text) < DESC_MIN:
            errors.append(f"{model}: слишком короткий ({len(text)} симв.)")
            continue
        text = _fix_opening(text)
        text = _ensure_example_links(text, suggested or examples_pool[:3])
        text = _fix_signature(text)
        if len(text) > DESC_MAX:
            cut = text[: DESC_MAX - 1].rsplit(" ", 1)[0]
            text = (cut or text[:DESC_MAX]) + "…"
        if len(text) < DESC_MIN:
            errors.append(f"{model}: после подписи слишком короткий ({len(text)} симв.)")
            continue
        return {"ok": True, "text": text, "model": model}

    return {
        "ok": False,
        "error": "Все модели не дали текст. " + " | ".join(errors[:4]),
    }
