"""Генерация текста отклика через RouterAI (OpenAI-compatible API)."""
from __future__ import annotations

import os
import re
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
        "temperature": 0.6,
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

    system = (
        "Ты пишешь отклик фрилансера на бирже Kwork от лица команды разработчиков.\n"
        "Главное — внимательно разобрать описание заказчика и писать отклик под его задачу, "
        "а не копировать общий шаблон.\n\n"
        "Как работать с ТЗ заказчика:\n"
        "1) Выдели суть: что нужно сделать, цели, ограничения, стек/платформы, интеграции, "
        "сроки/ожидания, если указаны.\n"
        "2) В первых 2–4 предложениях покажи, что понял именно их запрос "
        "(переформулируй ключевые требования своими словами).\n"
        "3) Предложи конкретный план/подход по их пунктам (2–5 коротких шагов или блоков работы).\n"
        "4) Из шаблона команды бери только релевантное: подходящие кейсы, стек, ссылки. "
        "Нерелевантное (другие типы проектов) не перечисляй списком.\n"
        "5) Не выдумывай опыт и кейсы, которых нет в шаблоне. "
        "Не обещай то, чего заказчик не просил.\n\n"
        "Стиль: русский язык, деловой и живой, без канцелярита и воды. "
        "Без markdown (** ##), без заголовков «Отклик:». "
        f"Длина строго {DESC_MIN}–{DESC_MAX} символов.\n"
        "НЕ пиши подпись «С уважением» и имя команды — их добавим отдельно. "
        "Запрещены плейсхолдеры в квадратных скобках и фразы «Ваше имя». "
        "Верни только текст отклика без пояснений и без разбора «шаг 1/2» для себя."
    )
    user = (
        f"Название проекта: {name}\n\n"
        f"=== ОПИСАНИЕ ЗАКАЗЧИКА (основа отклика) ===\n"
        f"{client_text or '(описание пустое — опирайся на название и релевантный опыт из шаблона)'}\n\n"
        f"=== МАТЕРИАЛЫ О КОМАНДЕ (факты/кейсы/ссылки — только по делу) ===\n"
        f"{template or '(шаблон пуст)'}\n\n"
        f"Подпись команды (в текст НЕ включать): {signature}\n\n"
        "Сначала мысленно разбери ТЗ, затем напиши один готовый персонализированный отклик "
        "под этого заказчика."
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
