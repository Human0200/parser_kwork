"""Автоотправка отклика на Kwork через Playwright."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv()


def _price_numbers(value: Any) -> list[int]:
    if value is None:
        return []
    text = str(value).replace("\u00a0", " ")
    nums: list[int] = []
    for match in re.finditer(r"\d+(?:\s\d{3})*(?:[.,]\d+)?", text):
        raw = match.group(0).replace(" ", "").replace(",", ".")
        try:
            amount = int(float(raw))
        except ValueError:
            continue
        if amount > 0:
            nums.append(amount)
    return nums


def _parse_price(value: Any) -> int | None:
    """Максимальная цена из строки бюджета."""
    nums = _price_numbers(value)
    return max(nums) if nums else None


def project_budget_bounds(project: dict[str, Any]) -> tuple[int | None, int | None]:
    """(min, max) бюджета объявления."""
    nums = _price_numbers(
        f"{project.get('price_limit') or ''} {project.get('possible_price_limit') or ''}"
    )
    if not nums:
        return None, None
    return min(nums), max(nums)


def project_offer_price(project: dict[str, Any]) -> int | None:
    """Цена для «Откликнуться»: среднее между min и max бюджета (500–1500 → 1000)."""
    low, high = project_budget_bounds(project)
    if low is None:
        return None
    if high is None or high == low:
        return low
    return (low + high) // 2


def project_max_price(project: dict[str, Any]) -> int | None:
    """Верхняя граница бюджета (для подсказки «Своя цена»)."""
    _, high = project_budget_bounds(project)
    return high


def build_response_text(project_name: str, description: str = "") -> str:
    template_file = os.getenv("KWORK_RESPONSE_TEMPLATE_FILE", "response_template.txt")
    template = ""
    if template_file and os.path.isfile(template_file):
        with open(template_file, encoding="utf-8") as fh:
            template = fh.read().strip()
    if not template:
        template = os.getenv(
            "KWORK_RESPONSE_TEMPLATE",
            "Здравствуйте!\n\nМогу выполнить проект «{project_name}».\n{details}\n\nС уважением, {about}",
        ).replace("\\n", "\n")
    about = os.getenv("KWORK_ABOUT", "Команда FlowTeam")
    details = "Учту требования из ТЗ и предложу согласовать детали перед стартом."
    try:
        return template.format(
            project_name=project_name or "",
            details=details,
            about=about,
        )
    except (KeyError, ValueError):
        return template


def _storage_state_path() -> Path:
    """Путь к сохранённой сессии Playwright (cookies + localStorage)."""
    raw = os.getenv("KWORK_STORAGE_STATE", "").strip()
    if raw:
        return Path(raw)
    return Path("kwork-state.json")


def _save_storage_state(context) -> Path:
    path = _storage_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    print(f"✓ Сессия Kwork сохранена: {path}")
    return path


def _is_logged_in(page) -> bool:
    """Проверка, что сессия живая (не редирект на /login)."""
    try:
        page.goto("https://kwork.ru/seller/projects", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(800)
        url = (page.url or "").lower()
        if "login" in url or "signup" in url:
            return False
        # На странице логина часто есть форма
        if page.locator("input[type='password']").count() and page.locator(
            "button[type='submit'], button.auth-form__button"
        ).count():
            # если явно форма входа — не залогинены
            if page.locator("text=Вход").count() and "kwork.ru/login" in url:
                return False
        return True
    except Exception:
        return False


def _login(page, context=None) -> None:
    login = os.getenv("KWORK_LOGIN", "")
    password = os.getenv("KWORK_PASSWORD", "")
    if not login or not password:
        raise RuntimeError("Нет KWORK_LOGIN/KWORK_PASSWORD в .env")

    page.goto("https://kwork.ru/login", wait_until="domcontentloaded", timeout=30000)
    login_input = page.locator(
        "input[name='login'], input[name='email'], input[placeholder*='Электронная почта'], input[type='email']"
    ).first
    password_input = page.locator(
        "input[name='password'], input[type='password']"
    ).first
    login_input.wait_for(state="visible", timeout=15000)
    login_input.fill(login)
    password_input.fill(password)
    page.locator("button[type='submit'], input[type='submit'], button.auth-form__button").first.click()
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1500)
    if "login" in page.url.lower():
        # Возможна CAPTCHA / 2FA
        raise RuntimeError(
            "Не удалось войти в Kwork (остались на /login). "
            "Поставьте KWORK_HEADLESS=0 и пройдите проверку вручную, либо сохраните storage state."
        )
    if context is not None:
        _save_storage_state(context)


def _ensure_auth(page, context) -> None:
    cookies_file = os.getenv("KWORK_COOKIES_FILE", "").strip()
    if cookies_file:
        import json

        with open(cookies_file, encoding="utf-8") as fh:
            context.add_cookies(json.load(fh))
        if _is_logged_in(page):
            _save_storage_state(context)
            return
        print("⚠️ Cookies устарели, пробуем логин/пароль…")
        _login(page, context)
        return

    # storage_state уже мог быть загружен в browser.new_context(...)
    state_path = _storage_state_path()
    if state_path.is_file():
        if _is_logged_in(page):
            print(f"✓ Сессия Kwork из файла: {state_path}")
            return
        print(f"⚠️ Сессия из {state_path} устарела, логинимся заново…")

    _login(page, context)


def _safe_click(locator, *, timeout: int = 5000) -> bool:
    """Клик только по видимому элементу; иначе False без долгого зависания."""
    try:
        if locator.count() == 0:
            return False
        target = locator.first
        if not target.is_visible():
            return False
        try:
            target.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        target.click(timeout=timeout, force=True)
        return True
    except Exception:
        return False


def _open_offer_form(page, project_id: int | str, context=None) -> None:
    """Открывает форму отклика напрямую через new_offer."""
    url = f"https://kwork.ru/new_offer?project={project_id}"
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    if "login" in page.url.lower():
        _login(page, context)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

    # Если редиректнули с формы — пробуем страницу проекта и кнопку
    if "new_offer" not in page.url:
        project_url = f"https://kwork.ru/projects/{project_id}"
        page.goto(project_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        offer_btn = page.locator(
            ".want-card__buttons .kw-button--green, "
            ".projects-offer-btn.kw-button--green, "
            "div.kw-button--green, button.kw-button--green"
        ).filter(has_text=re.compile(r"Предложить услугу", re.I))
        if not _safe_click(offer_btn):
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)

    if "new_offer" not in page.url and page.locator(".trumbowyg-editor, #offer-custom-price").count() == 0:
        raise RuntimeError(
            f"Форма отклика не открылась (редирект на {page.url}). "
            "Возможно, проект закрыт, уже есть отклик или закончились коннекты."
        )

    page.locator(".trumbowyg-editor, #offer-custom-price").first.wait_for(
        state="visible", timeout=25000
    )


def _fill_editor(page, text: str) -> None:
    editor = page.locator(".trumbowyg-editor").first
    editor.wait_for(state="visible", timeout=25000)
    try:
        editor.click(timeout=5000)
    except Exception:
        editor.click(force=True, timeout=5000)
    page.evaluate(
        """(payload) => {
            const el = document.querySelector('.trumbowyg-editor');
            if (!el) throw new Error('Редактор не найден');
            el.focus();
            const html = payload.split(/\\n/).map(line => `<div>${line || '<br>'}</div>`).join('');
            el.innerHTML = html;
            el.classList.remove('is-placeholder-mobile', 'force-placeholder');
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        text,
    )


def _fill_price(page, price: int) -> None:
    """Заполняет стоимость так, чтобы Vue/v-model принял значение."""
    price_s = str(int(price))
    # Иногда нужно сначала выбрать «своя цена»
    for hint in (
        page.get_by_text(re.compile(r"Своя цена|Указать цену|Другая цена", re.I)),
        page.locator("label, button, div, span").filter(has_text=re.compile(r"^Своя цена$", re.I)),
    ):
        _safe_click(hint, timeout=1500)

    candidates = [
        page.locator("#offer-custom-price"),
        page.locator("input[name*='price' i], input[id*='price' i]"),
        page.get_by_placeholder(re.compile(r"стоимость|цена|\d", re.I)),
        page.locator(
            "xpath=//*[contains(normalize-space(.),'Стоимость')]"
            "/following::input[not(@type='hidden')][1]"
        ),
    ]
    field = None
    for loc in candidates:
        try:
            if loc.count() == 0:
                continue
            cand = loc.first
            if cand.is_visible():
                field = cand
                break
        except Exception:
            continue
    if field is None:
        raise RuntimeError("Поле «Стоимость» не найдено")

    field.wait_for(state="visible", timeout=15000)
    try:
        field.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    field.click(force=True, timeout=5000)
    field.fill("")
    field.type(price_s, delay=20)
    field.dispatch_event("input")
    field.dispatch_event("change")
    field.press("Tab")
    page.wait_for_timeout(200)

    current = ""
    try:
        current = re.sub(r"\D", "", field.input_value() or "")
    except Exception:
        current = ""
    if current != price_s:
        # Vue-совместимый setter
        ok = page.evaluate(
            """(price) => {
                const pick = () => {
                    const byId = document.querySelector('#offer-custom-price');
                    if (byId) return byId;
                    const inputs = Array.from(document.querySelectorAll('input')).filter(i => {
                        const r = i.getBoundingClientRect();
                        const s = getComputedStyle(i);
                        if (r.width < 40 || s.display === 'none' || s.visibility === 'hidden') return false;
                        if (['hidden','checkbox','radio','submit','button'].includes(i.type || '')) return false;
                        if (i.classList.contains('vs__search')) return false;
                        return /price|cost|стоим/i.test(i.id + i.name + (i.placeholder || ''))
                            || i.inputMode === 'numeric'
                            || i.type === 'number';
                    });
                    return inputs[0] || null;
                };
                const el = pick();
                if (!el) return false;
                el.focus();
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(el, price); else el.value = price;
                el.dispatchEvent(new InputEvent('input', { bubbles: true, data: price, inputType: 'insertText' }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                return String(el.value || '').replace(/\\D/g, '') === String(price);
            }""",
            price_s,
        )
        if not ok:
            raise RuntimeError(f"Не удалось ввести стоимость {price_s}")


def _order_name_required(page) -> bool:
    """Есть ли видимый блок «Название заказа» (часто Trumbowyg, не input)."""
    return bool(
        page.evaluate(
            """() => {
                const block = document.querySelector('.modal-individual-offer__name');
                if (block) {
                    const s = getComputedStyle(block);
                    if (s.display !== 'none' && s.visibility !== 'hidden' && block.offsetParent !== null) {
                        return true;
                    }
                }
                const lab = Array.from(document.querySelectorAll('label')).find(el =>
                    (el.textContent || '').replace(/\\s+/g, ' ').trim() === 'Название заказа'
                );
                if (!lab) return false;
                const root = lab.closest('.modal-individual-offer__name') || lab.parentElement;
                if (!root) return false;
                const s = getComputedStyle(root);
                return s.display !== 'none' && s.visibility !== 'hidden';
            }"""
        )
    )


def _fill_order_name(page, name: str) -> None:
    """Поле «Название заказа» — Trumbowyg contenteditable / textarea[name=name]."""
    if not _order_name_required(page):
        return

    title = (name or "Выполнение задачи").strip()
    title = re.sub(r"\s+", " ", title)[:70]
    if not title:
        title = "Выполнение задачи"
    page.wait_for_timeout(200)

    # 1) contenteditable в блоке названия
    editor = page.locator(
        ".modal-individual-offer__name .trumbowyg-editor, "
        ".modal-individual-offer__name [contenteditable='true']"
    ).first
    if editor.count() and editor.is_visible():
        try:
            editor.click(force=True, timeout=3000)
            page.evaluate(
                """(title) => {
                    const root = document.querySelector('.modal-individual-offer__name');
                    const el = root && (root.querySelector('.trumbowyg-editor')
                        || root.querySelector('[contenteditable="true"]'));
                    if (!el) return false;
                    el.focus();
                    el.classList.remove('is-placeholder-mobile', 'force-placeholder');
                    el.innerHTML = `<div>${title}</div>`;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    const ta = root.querySelector('textarea[name="name"]');
                    if (ta) {
                        ta.value = title;
                        ta.dispatchEvent(new Event('input', { bubbles: true }));
                        ta.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    return true;
                }""",
                title,
            )
            page.wait_for_timeout(200)
            current = (editor.inner_text() or "").strip()
            if current:
                return
        except Exception:
            pass

    # 2) скрытый textarea name=name
    filled = page.evaluate(
        """(title) => {
            const root = document.querySelector('.modal-individual-offer__name');
            if (!root) return {ok: false, reason: 'no_block'};
            const style = getComputedStyle(root);
            if (style.display === 'none') return {ok: false, reason: 'hidden'};
            const ta = root.querySelector('textarea[name="name"]');
            const el = root.querySelector('.trumbowyg-editor, [contenteditable="true"]');
            if (el) {
                el.focus();
                el.classList.remove('is-placeholder-mobile', 'force-placeholder');
                el.innerHTML = '<div>' + title + '</div>';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (ta) {
                const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
                if (setter) setter.call(ta, title); else ta.value = title;
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                ta.dispatchEvent(new Event('change', { bubbles: true }));
            }
            const text = (el && (el.innerText || '').trim()) || (ta && ta.value) || '';
            return {ok: !!text, value: text};
        }""",
        title,
    )
    if isinstance(filled, dict) and filled.get("ok"):
        return

    raise RuntimeError(
        f"Не удалось заполнить «Название заказа» (попытка: {title!r}). "
        f"JS: {filled}"
    )


def _payment_order_selected(page) -> bool:
    try:
        if page.locator(".offer-payment-type__item.active").count() > 0:
            return True
    except Exception:
        pass
    return bool(
        page.evaluate(
            """() => {
                const items = Array.from(document.querySelectorAll('.offer-payment-type__item'));
                if (items.some(c => c.classList.contains('active'))) return true;
                return false;
            }"""
        )
    )


def _select_payment_order(page) -> None:
    """Обязательный блок «Желаемый порядок оплаты» — выбираем «Целиком».

    Класс active на загрузке часто декоративный: Vue/родительская форма
    не считает выбор сделанным, пока не было реального клика.
    Поэтому всегда делаем toggle: По мере → Целиком.
    """
    if page.locator(".offer-payment-type__item").count() == 0:
        if page.locator("text=Желаемый порядок оплаты").count() == 0:
            return

    stages = page.locator(".offer-payment-type__item").filter(
        has_text=re.compile(r"По мере", re.I)
    ).first
    full = page.locator(".offer-payment-type__item").filter(
        has_text=re.compile(r"Целиком", re.I)
    ).first

    # Toggle, чтобы точно триггернуть Vue
    if stages.count():
        _safe_click(stages, timeout=3000)
        page.wait_for_timeout(250)
    if full.count():
        _safe_click(full, timeout=3000)
        page.wait_for_timeout(350)
    else:
        page.evaluate(
            """() => {
                const items = Array.from(document.querySelectorAll('.offer-payment-type__item'));
                const stages = items.find(n => /По мере/i.test(n.textContent || ''));
                const all = items.find(n => /Целиком/i.test(n.textContent || ''));
                if (stages) stages.click();
                if (all) all.click();
            }"""
        )
        page.wait_for_timeout(350)

    # Через Vue data на всякий случай
    page.evaluate(
        """() => {
            const root = document.querySelector('.offer-payment-type');
            const vm = root && root.__vue__;
            if (vm && 'offerPayment' in vm) {
                vm.offerPayment = vm.offerPaymentAll || 'all';
            }
        }"""
    )
    page.wait_for_timeout(200)


def _select_deadline(page, days: int) -> None:
    """Выбор срока: клик по select у «Срок выполнения» или ввод числа в vs__search."""
    # Находим корневой v-select рядом с лейблом
    root_found = page.evaluate(
        """() => {
            const nodes = Array.from(document.querySelectorAll('label, div, span, p, h3, h4'));
            const label = nodes.find(el => {
                const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                return t === 'Срок выполнения' || t.startsWith('Срок выполнения');
            });
            if (!label) return false;
            let root = label.closest('.v-select') || label.parentElement;
            for (let i = 0; i < 6 && root; i++) {
                if (root.querySelector && root.querySelector('.vs__dropdown-toggle, input.vs__search, select')) {
                    root.setAttribute('data-offer-duration', '1');
                    return true;
                }
                root = root.parentElement;
            }
            // соседние блоки
            let sib = label.parentElement;
            for (let i = 0; i < 4 && sib; i++) {
                const vs = sib.querySelector('.v-select, .vs__dropdown-toggle');
                if (vs) {
                    (vs.closest('.v-select') || vs).setAttribute('data-offer-duration', '1');
                    return true;
                }
                sib = sib.parentElement;
            }
            return false;
        }"""
    )

    duration = page.locator('[data-offer-duration="1"]')
    if not root_found or duration.count() == 0:
        # Часто срок — последний v-select в форме предложения
        duration = page.locator("form .v-select, .want-offer-form .v-select, .offer-form .v-select").last
        if duration.count() == 0:
            duration = page.locator(".v-select").last

    if duration.count() == 0:
        raise RuntimeError("Поле «Срок выполнения» не найдено")

    # 1) Пробуем ввести число в поиск vue-select и нажать Enter
    search = duration.locator("input.vs__search").first
    if search.count() == 0:
        search = page.locator("[data-offer-duration='1'] input.vs__search, .v-select input.vs__search").last

    if search.count():
        try:
            search.click(force=True, timeout=3000)
            search.fill("")
            search.type(str(days), delay=30)
            page.wait_for_timeout(400)
            # выбрать совпадение из открывшегося списка
            opt = page.locator(".vs__dropdown-option").filter(
                has_text=re.compile(rf"^\s*{days}\s*(день|дня|дней)?\b", re.I)
            ).first
            if opt.count() and opt.is_visible():
                opt.click(force=True, timeout=3000)
                return
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            # Проверяем, что значение выбрано
            selected = duration.inner_text()
            if str(days) in (selected or ""):
                return
        except Exception:
            pass

    # 2) Открыть dropdown и кликнуть опцию
    toggle = duration.locator(".vs__dropdown-toggle, .vs__actions, .vs__open-indicator").first
    if toggle.count() == 0:
        toggle = duration
    try:
        toggle.click(force=True, timeout=4000)
    except Exception:
        page.evaluate(
            """() => {
                const root = document.querySelector('[data-offer-duration="1"]') || document.querySelector('.v-select:last-of-type');
                const t = root && (root.querySelector('.vs__dropdown-toggle') || root);
                if (t) t.click();
            }"""
        )
    page.wait_for_timeout(500)

    # Опции могут рендериться в портале у body
    option_sel = ".vs__dropdown-menu .vs__dropdown-option, .vs__dropdown-option, [role='listbox'] [role='option']"
    try:
        page.locator(option_sel).first.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError:
        # ещё одна попытка: клик по open-indicator
        ind = duration.locator(".vs__open-indicator").first
        if ind.count():
            try:
                ind.click(force=True, timeout=2000)
                page.wait_for_timeout(500)
                page.locator(option_sel).first.wait_for(state="visible", timeout=5000)
            except Exception as exc:
                raise RuntimeError(
                    f"Список сроков не открылся. Убедитесь, что форма new_offer загружена. ({exc})"
                ) from exc

    pattern = re.compile(rf"^\s*{days}\s*(день|дня|дней)\b", re.IGNORECASE)
    options = page.locator(option_sel)
    for i in range(options.count()):
        opt = options.nth(i)
        try:
            if not opt.is_visible():
                continue
            label = (opt.inner_text() or "").strip()
        except Exception:
            continue
        if pattern.search(label) or label == str(days) or re.match(rf"^{days}\b", label):
            opt.click(force=True, timeout=3000)
            return

    raise RuntimeError(f"Не найден срок выполнения: {days} дн.")


def _click_submit(page) -> None:
    submit_btn = page.locator("button.kw-button.kw-button--green, button.kw-button--green").filter(
        has_text=re.compile(r"Предложить", re.I)
    )
    if not _safe_click(submit_btn, timeout=8000):
        # JS fallback
        clicked = page.evaluate(
            """() => {
                const buttons = Array.from(document.querySelectorAll('button, .kw-button'));
                const btn = buttons.find(b => /предложить/i.test((b.textContent || '').trim())
                    && !/услугу/i.test((b.textContent || '')));
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        )
        if not clicked:
            raise RuntimeError("Кнопка «Предложить» не найдена")
    page.wait_for_timeout(1500)

    # Kwork часто показывает предупреждение о шаблонном тексте
    confirm = page.locator("button, .kw-button, a").filter(
        has_text=re.compile(r"Отправить как есть", re.I)
    )
    if not _safe_click(confirm, timeout=5000):
        page.evaluate(
            """() => {
                const el = Array.from(document.querySelectorAll('button, .kw-button, a'))
                    .find(b => /отправить как есть/i.test(b.textContent || ''));
                if (el) el.click();
            }"""
        )
    page.wait_for_timeout(2500)


def _collect_form_errors(page) -> list[str]:
    """Красные/валидационные сообщения на форме new_offer."""
    errors = page.evaluate(
        """() => {
            const texts = new Set();
            const isValidation = (t) =>
                /введите|выберите|укажите|обязатель|не более|не менее|заполн|ошибк|стоимость может|название заказа|порядок оплаты/i.test(t);
            const push = (t) => {
                const s = (t || '').replace(/\\s+/g, ' ').trim();
                if (!s || s.length > 160) return;
                // Не тащим заголовок проекта / длинные описания
                if (!isValidation(s) && s.length > 60) return;
                if (!isValidation(s) && !/стоимость|цена|срок|назван|оплат/i.test(s)) return;
                texts.add(s);
            };
            document.querySelectorAll(
                '.error, .field-error, .form-error, .invalid-feedback, .help-block, [class*="error"]'
            ).forEach(el => {
                const style = getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return;
                // class*="error" часто вешает на большие блоки — берём только короткий текст
                const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (t.length <= 160) push(t);
            });
            document.querySelectorAll('div, span, p, label, small').forEach(el => {
                if (el.children && el.children.length > 2) return;
                const style = getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return;
                const color = style.color || '';
                const rgb = color.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                if (!rgb) return;
                const r = +rgb[1], g = +rgb[2], b = +rgb[3];
                if (r < 160 || g > 120 || b > 120) return;
                const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                push(t);
            });
            return Array.from(texts);
        }"""
    )
    return [e for e in (errors or []) if e]


def _ensure_required_fields(page, price: int, order_name: str) -> None:
    """Перед отправкой дозаполняет оплату/название/цену."""
    # Оплата первой: от неё зависит видимость «Название заказа»
    if page.locator(".offer-payment-type__item").count() > 0:
        _select_payment_order(page)

    if _order_name_required(page):
        _fill_order_name(page, order_name)

    price_ok = page.evaluate(
        """(price) => {
            const el = document.querySelector('#offer-custom-price');
            if (!el) return false;
            return String(el.value || '').replace(/\\D/g, '') === String(price);
        }""",
        str(int(price)),
    )
    if not price_ok:
        _fill_price(page, price)
        still = page.evaluate(
            """() => {
                const el = document.querySelector('#offer-custom-price');
                return el ? String(el.value || '').replace(/\\D/g, '') : '';
            }"""
        )
        if still != str(int(price)):
            raise RuntimeError(f"Стоимость не сохранилась в форме (сейчас {still!r}, нужно {price})")


def _verify_offer_submitted(page, project_id: int | str) -> None:
    """Подтверждает, что отклик реально ушёл; иначе бросает RuntimeError."""
    url = (page.url or "").lower()
    errors = _collect_form_errors(page)

    price_field = page.locator("#offer-custom-price")
    price_visible = False
    try:
        price_visible = price_field.count() > 0 and price_field.first.is_visible()
    except Exception:
        price_visible = False

    still_on_form = "new_offer" in url or price_visible

    if errors and still_on_form:
        raise RuntimeError("Форма не принята: " + "; ".join(errors[:3]))

    # Успех: ушли с формы / появилось подтверждение
    success_hint = page.evaluate(
        """() => {
            const body = (document.body && document.body.innerText) || '';
            return /ваш отклик|предложение отправлено|вы предложили|уже откликнулись/i.test(body);
        }"""
    )
    if success_hint:
        return

    if "new_offer" not in url and not price_visible and not errors:
        return

    if still_on_form:
        raise RuntimeError(
            "Отклик не подтверждён: форма всё ещё открыта "
            f"(url={page.url}). Возможно, ошибка валидации или не хватило коннектов."
        )


def submit_offer(
    project_id: int | str,
    response_text: str,
    price: int,
    deadline_days: int,
    *,
    order_name: str = "",
    dry_run: bool = False,
    screenshot_dir: str = "debug_screens",
) -> dict[str, Any]:
    """Открывает форму отклика, заполняет поля и нажимает «Предложить».

    dry_run=True — только заполняет форму, без финального клика.
    """
    if not price or price <= 0:
        return {
            "ok": False,
            "project_id": str(project_id),
            "error": f"Некорректная цена: {price}",
        }

    headless = os.getenv("KWORK_HEADLESS", "1").lower() not in {"0", "false", "no"}
    state_path = _storage_state_path()
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    shot = Path(screenshot_dir) / f"offer_{project_id}.png"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context_kwargs: dict[str, Any] = {}
        if state_path.is_file():
            context_kwargs["storage_state"] = str(state_path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        try:
            _ensure_auth(page, context)
            _open_offer_form(page, project_id, context)

            offer_title = (order_name or "").strip() or f"Проект {project_id}"
            offer_title = re.sub(r"\s+", " ", offer_title)[:70]

            _fill_editor(page, response_text)
            _fill_price(page, price)
            _select_payment_order(page)
            _fill_order_name(page, offer_title)
            _select_deadline(page, deadline_days)
            # Vue часто сбрасывает цену/оплату после других полей — дозаполняем
            _ensure_required_fields(page, price, offer_title)
            page.screenshot(path=str(shot), full_page=True)

            if dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "project_id": str(project_id),
                    "price": price,
                    "deadline_days": deadline_days,
                    "url": page.url,
                    "screenshot": str(shot),
                }

            _click_submit(page)
            retry_errors = _collect_form_errors(page)
            need_retry = any(
                any(k in e.lower() for k in ("назван", "стоим", "оплат", "введите", "выберите"))
                for e in retry_errors
            )
            if need_retry:
                _ensure_required_fields(page, price, offer_title)
                _click_submit(page)
            page.screenshot(path=str(shot), full_page=True)
            _verify_offer_submitted(page, project_id)
            page.screenshot(path=str(shot), full_page=True)
            return {
                "ok": True,
                "project_id": str(project_id),
                "price": price,
                "deadline_days": deadline_days,
                "url": page.url,
                "screenshot": str(shot),
            }
        except Exception as exc:
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            return {
                "ok": False,
                "project_id": str(project_id),
                "error": str(exc),
                "url": getattr(page, "url", ""),
                "screenshot": str(shot) if shot.exists() else None,
            }
        finally:
            context.close()
            browser.close()
