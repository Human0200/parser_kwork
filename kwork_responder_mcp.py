"""Safe Kwork response assistant (draft-only).

Fetches projects using the existing parser, filters by configured expertise,
chooses the lower bound of a Kwork budget range, and stores personalized
response drafts. It deliberately does not submit proposals; submission must
be implemented only against an authorized, documented Kwork API.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

load_dotenv()


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def _price(value: Any) -> int | None:
    """Parse the largest numeric amount from Kwork price text."""
    if value is None:
        return None
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
    return max(nums) if nums else None


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS responses (
            project_id TEXT PRIMARY KEY, name TEXT, sphere TEXT, min_price INTEGER,
            max_price INTEGER, sent_price INTEGER, response TEXT, created_at TEXT,
            status TEXT NOT NULL)""")
        self.db.commit()

    def seen(self, project_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM responses WHERE project_id=?", (project_id,)).fetchone() is not None

    def save(self, row: dict[str, Any]) -> None:
        self.db.execute("INSERT OR IGNORE INTO responses VALUES (?,?,?,?,?,?,?,?,?)", tuple(row[k] for k in (
            "project_id", "name", "sphere", "min_price", "max_price", "sent_price", "response", "created_at", "status")))
        self.db.commit()

    def get(self, project_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM responses WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            return None
        return dict(zip([d[0] for d in self.db.description], row))

    def update_status(self, project_id: str, status: str) -> None:
        self.db.execute("UPDATE responses SET status=? WHERE project_id=?", (status, project_id))
        self.db.commit()


CFG = {
    "spheres": _csv("KWORK_SPHERES"),
    "skills": _csv("KWORK_SKILLS"),
    "exclude": _csv("KWORK_EXCLUDE"),
    "template": os.getenv("KWORK_RESPONSE_TEMPLATE", "Здравствуйте!\n\nГотов выполнить задачу: {project_name}.\n{details}\n\nС уважением, {about}"),
    "about": os.getenv("KWORK_ABOUT", "исполнитель"),
    "deadline": os.getenv("KWORK_DEFAULT_DEADLINE", ""),
    "db": os.getenv("KWORK_DB", "kwork_responses.db"),
    "projects_url": os.getenv("KWORK_PROJECTS_URL", "https://kwork.ru/projects?page={page}"),
    "storage_state": os.getenv("KWORK_STORAGE_STATE", ""),
    "cookies_file": os.getenv("KWORK_COOKIES_FILE", ""),
    "headless": os.getenv("KWORK_HEADLESS", "1").lower() not in {"0", "false", "no"},
    "login": os.getenv("KWORK_LOGIN", ""),
    "password": os.getenv("KWORK_PASSWORD", ""),
}
MAX_OFFERS = 8
store = Store(CFG["db"])
mcp = FastMCP("kwork-response-assistant")


def _match(project: dict[str, Any]) -> tuple[str | None, list[str]]:
    text = f"{project.get('name', '')} {project.get('description', '')}".lower()
    if any(x.lower() in text for x in CFG["exclude"]):
        return None, []
    try:
        offers = int(str(project.get("offers_count")).replace(" ", "").replace("\u00a0", ""))
    except (TypeError, ValueError):
        return None, []
    if offers > MAX_OFFERS:
        return None, []
    hits = [s for s in CFG["spheres"] if s.lower() in text]
    skill_hits = [s for s in CFG["skills"] if s.lower() in text]
    return (hits[0] if hits else None), skill_hits


def _draft(project: dict[str, Any], sphere: str, skills: list[str]) -> str:
    details = f"Сфера: {sphere}."
    if skills:
        details += " Релевантные навыки: " + ", ".join(skills) + "."
    if project.get("description"):
        details += " Учту требования из ТЗ и предложу согласовать детали перед стартом."
    if CFG["deadline"]:
        details += f" Срок: {CFG['deadline']}."
    return CFG["template"].format(project_name=project.get("name", ""), details=details, about=CFG["about"])


def _extract_state_projects(html: str) -> list[dict[str, Any]]:
    """Extract Kwork's embedded stateData from a rendered page."""
    match = re.search(r"window\.stateData\s*=\s*({.*?});", html, re.DOTALL)
    if not match:
        return []
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    wants = state.get("wantsListData", {})
    return wants.get("pagination", {}).get("data", []) or wants.get("wants", []) or []


async def _browser_projects(page_number: int) -> list[dict[str, Any]]:
    """Read projects using Playwright, optionally authenticated by storage state."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=CFG["headless"])
        context_kwargs: dict[str, Any] = {}
        if CFG["storage_state"]:
            context_kwargs["storage_state"] = CFG["storage_state"]
        context = await browser.new_context(**context_kwargs)
        if CFG["cookies_file"]:
            with open(CFG["cookies_file"], encoding="utf-8") as fh:
                cookies = json.load(fh)
            # Cookie-Editor exports a list; Playwright accepts it directly.
            await context.add_cookies(cookies)
        page = await context.new_page()
        try:
            # Prefer an existing authenticated state. Fall back to the normal
            # Kwork login form when credentials are provided locally.
            if not CFG["storage_state"] and not CFG["cookies_file"] and CFG["login"] and CFG["password"]:
                await page.goto("https://kwork.ru/login", wait_until="domcontentloaded", timeout=30000)
                login = page.locator("input[name='login'], input[name='email'], input[placeholder*='Электронная почта']").first
                password = page.locator("input[name='password'], input[type='password'], input[placeholder='Пароль']").first
                await login.fill(CFG["login"])
                await password.fill(CFG["password"])
                submit = page.locator("button[type='submit'], input[type='submit'], button.auth-form__button").first
                await submit.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
                if "login" in page.url.lower():
                    raise RuntimeError("Kwork login did not complete (check CAPTCHA/2FA with KWORK_HEADLESS=0)")
            await page.goto(CFG["projects_url"].format(page=page_number), wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            return _extract_state_projects(await page.content())
        except PlaywrightTimeoutError:
            # The embedded state is usually present even if networkidle never settles.
            return _extract_state_projects(await page.content())
        finally:
            await context.close()
            await browser.close()


@mcp.tool()
async def scan_projects(page: int = 1) -> str:
    """Fetch one page and create personalized, non-submitted response drafts."""
    result = []
    for raw in await _browser_projects(page):
        p = {
            "id": raw.get("id"),
            "name": raw.get("name", ""),
            "description": raw.get("description", ""),
            "price_limit": raw.get("priceLimit", ""),
            "possible_price_limit": raw.get("possiblePriceLimit", ""),
            "offers_count": raw.get("offers_count", raw.get("offersCount", raw.get("views_dirty"))),
        }
        pid = str(p.get("id", ""))
        if not pid or store.seen(pid):
            continue
        sphere, skills = _match(p)
        if not sphere:
            continue
        source = f"{p.get('price_limit', '')} {p.get('possible_price_limit', '')}"
        maximum = _price(source)
        # min для журнала — отдельно, без нулей из дробной части
        parts = [int(float(x.replace(" ", "").replace(",", ".")))
                 for x in re.findall(r"\d+(?:\s\d{3})*(?:[.,]\d+)?", source.replace("\u00a0", " "))
                 if float(x.replace(" ", "").replace(",", ".")) > 0]
        minimum = min(parts) if parts else maximum
        draft = _draft(p, sphere, skills)
        row = {"project_id": pid, "name": p.get("name", ""), "sphere": sphere, "min_price": minimum,
               "max_price": maximum, "sent_price": maximum, "response": draft,
               "created_at": datetime.now(timezone.utc).isoformat(), "status": "draft"}
        store.save(row)
        result.append(row)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def submit_response(project_id: str, confirm: bool = False) -> str:
    """Submit one stored draft through Kwork's browser form.

    Requires confirm=True. The project must first be present in response_log.
    """
    if not confirm:
        return "Отклик не отправлен: для подтверждения передайте confirm=True."
    row = store.get(str(project_id))
    if not row:
        return "Отклик не найден. Сначала вызовите scan_projects."
    if row["sent_price"] is None:
        return "Отклик не отправлен: в проекте не определена допустимая цена."
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=CFG["headless"])
            context_kwargs: dict[str, Any] = {}
            if CFG["storage_state"]:
                context_kwargs["storage_state"] = CFG["storage_state"]
            context = await browser.new_context(**context_kwargs)
            if CFG["cookies_file"]:
                with open(CFG["cookies_file"], encoding="utf-8") as fh:
                    await context.add_cookies(json.load(fh))
            page = await context.new_page()
            await page.goto(f"https://kwork.ru/projects/{project_id}", wait_until="domcontentloaded", timeout=30000)
            text_area = page.locator("textarea[name='comment'], textarea[name='description'], textarea.js-offer-comment").first
            price_input = page.locator("input[name='price'], input.js-offer-price").first
            if not text_area.count() or not price_input.count():
                raise RuntimeError("Форма отклика не найдена или пользователь не авторизован")
            await text_area.fill(row["response"])
            await price_input.fill(str(row["sent_price"]))
            deadline = page.locator("input[name='duration'], input[name='deadline'], input.js-offer-duration").first
            if CFG["deadline"] and deadline.count():
                await deadline.fill(CFG["deadline"])
            submit = page.locator("button.js-offer-submit, button[type='submit'].js-send-offer, .js-offer-form button[type='submit']").first
            if not submit.count():
                raise RuntimeError("Кнопка отправки отклика не найдена")
            await submit.click()
            await page.wait_for_timeout(1500)
            await context.close()
            await browser.close()
        store.update_status(str(project_id), "submitted")
        return json.dumps({"project_id": project_id, "status": "submitted", "price": row["sent_price"]}, ensure_ascii=False)
    except Exception as exc:
        store.update_status(str(project_id), "failed")
        return json.dumps({"project_id": project_id, "status": "failed", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def response_log(limit: int = 50) -> str:
    """Return stored response log; status is always draft until an authorized integration exists."""
    rows = store.db.execute("SELECT * FROM responses ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return json.dumps([dict(zip([d[0] for d in store.db.description], r)) for r in rows], ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
