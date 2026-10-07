"""Обработка кнопок «Автоотклик» / «Свой отклик» в Telegram."""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from ai_description import generate_offer_description
from offer_automation import (
    build_response_text,
    project_budget_bounds,
    project_max_price,
    project_min_offer_price,
    project_offer_price,
    project_price_presets,
    submit_offer,
)
from telegram_bot import TelegramBot

DEADLINE_CHOICES = (1, 2, 3, 5, 7, 10, 14)
DESC_MIN_LEN = 150
DESC_MAX_LEN = 2000


class OfferListener:
    """Слушает callback-кнопки и текстовые ответы."""

    def __init__(self, bot: TelegramBot, db_path: str = "kwork_projects.db"):
        self.bot = bot
        self.db_path = db_path
        self.offset = 0
        self.pending: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.project_cache: Dict[str, Dict[str, Any]] = {}
        # project_id -> {chat_id, message_id, html}
        self.announcements: Dict[str, Dict[str, Any]] = {}

    def cache_project(self, project: Dict[str, Any]) -> None:
        pid = project.get("id")
        if pid is not None:
            self.project_cache[str(pid)] = project

    def remember_announcement(
        self,
        project_id: str | int,
        chat_id: str,
        message_id: int,
        html_text: str = "",
    ) -> None:
        self.announcements[str(project_id)] = {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "html": html_text or "",
        }

    def _announcement_ref(
        self, project_id: str, state: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        if state:
            mid = state.get("announce_message_id")
            cid = state.get("announce_chat_id")
            if mid and cid:
                return {
                    "chat_id": str(cid),
                    "message_id": int(mid),
                    "html": state.get("announce_html")
                    or (self.announcements.get(str(project_id)) or {}).get("html", ""),
                }
        return self.announcements.get(str(project_id))

    def _mark_announcement_done(self, project_id: str, state: Optional[Dict[str, Any]] = None) -> None:
        """Убирает кнопки у объявления и ставит галочку об успешном отклике."""
        ref = self._announcement_ref(project_id, state)
        if not ref:
            return
        html = (ref.get("html") or "").strip()
        if html.lstrip().startswith("✅"):
            body = html
        elif html:
            body = f"✅ <b>Отклик отправлен</b>\n\n{html}"
        else:
            body = "✅ <b>Отклик отправлен</b>"
        self.bot.edit_message_text(
            ref["chat_id"],
            ref["message_id"],
            body,
            reply_markup={"inline_keyboard": []},
        )
        self.announcements[str(project_id)] = {**ref, "html": body}

    def _restore_announcement_buttons(
        self, project_id: str, state: Optional[Dict[str, Any]] = None
    ) -> None:
        ref = self._announcement_ref(project_id, state)
        if not ref:
            return
        self.bot.edit_message_reply_markup(
            ref["chat_id"],
            ref["message_id"],
            self.bot.offer_buttons(project_id),
        )

    def _bind_announcement_from_callback(
        self, state: Dict[str, Any], project_id: str, cq: Dict[str, Any]
    ) -> None:
        msg = cq.get("message") or {}
        mid = msg.get("message_id")
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        if not mid or not chat_id:
            return
        state["announce_message_id"] = int(mid)
        state["announce_chat_id"] = chat_id
        cached = self.announcements.get(str(project_id)) or {}
        html = cached.get("html") or ""
        if not html:
            # fallback: plain text из Telegram (без HTML-разметки)
            plain = (msg.get("text") or "").strip()
            if plain and not plain.startswith("✅"):
                html = self.bot._escape_html(plain)
        state["announce_html"] = html
        self.remember_announcement(project_id, chat_id, int(mid), html)
        # на время сценария убираем кнопки, чтобы не жали повторно
        self.bot.edit_message_reply_markup(chat_id, int(mid), {"inline_keyboard": []})

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telegram-offer-listener", daemon=True)
        self._thread.start()
        print("✓ Слушатель кнопок Telegram запущен")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                updates = self.bot.get_updates(offset=self.offset, timeout=25)
                for update in updates:
                    self.offset = max(self.offset, update["update_id"] + 1)
                    self._handle_update(update)
            except Exception as exc:
                print(f"❌ Ошибка слушателя Telegram: {exc}")
                time.sleep(3)

    def _handle_update(self, update: Dict[str, Any]) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        if "message" in update:
            self._handle_message(update["message"])

    def _allowed_chat(self, chat_id: Any) -> bool:
        return str(chat_id) == str(self.bot.chat_id)

    def _deadline_keyboard(self, project_id: str) -> Dict[str, Any]:
        row1 = [{"text": f"{d} дн.", "callback_data": f"deadline:{project_id}:{d}"} for d in DEADLINE_CHOICES[:4]]
        row2 = [{"text": f"{d} дн.", "callback_data": f"deadline:{project_id}:{d}"} for d in DEADLINE_CHOICES[4:]]
        row2.append({"text": "Другое", "callback_data": f"deadline_custom:{project_id}"})
        return {"inline_keyboard": [row1, row2]}

    def _price_keyboard(self, project_id: str, project: Dict[str, Any]) -> Dict[str, Any]:
        """Кнопки цены: от (−20% минимума бюджета) до максимума."""
        prices = project_price_presets(project)
        rows: list[list[Dict[str, str]]] = []
        row: list[Dict[str, str]] = []
        for price in prices:
            row.append({"text": f"{price} ₽", "callback_data": f"price:{project_id}:{price}"})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "Другая сумма", "callback_data": f"price_custom:{project_id}"}])
        return {"inline_keyboard": rows}

    def _ask_deadline(self, chat_id: str, project_id: str, price: int, name: str) -> None:
        self.bot.send_message(
            f"Цена: <b>{price}</b> ₽\n"
            f"Проект: <b>{self.bot._escape_html(str(name))}</b>\n\n"
            f"Выберите срок выполнения:",
            chat_id=chat_id,
            reply_markup=self._deadline_keyboard(project_id),
        )

    def _start_description_step(self, chat_id: str, project_id: str, state: Dict[str, Any]) -> None:
        """После цены и срока — описание (ИИ или своё)."""
        project = state.get("project") or self._load_project(project_id)
        if not project:
            self.bot.send_message("Проект не найден.", chat_id=chat_id)
            return
        price = state.get("price")
        days = state.get("days")
        name = project.get("name", project_id)

        self.bot.send_message(
            f"Генерирую описание ИИ для: <b>{self.bot._escape_html(str(name))}</b>…",
            chat_id=chat_id,
        )
        ai = generate_offer_description(
            str(name),
            project.get("description", "") or "",
            price=int(price) if price else None,
            days=int(days) if days else None,
        )
        if not ai.get("ok"):
            state["awaiting"] = "custom_description"
            state["ai_description"] = None
            self.pending[chat_id] = state
            self.bot.send_message(
                f"ИИ не смог сгенерировать текст: "
                f"{self.bot._escape_html(str(ai.get('error', 'unknown')))}\n\n"
                f"Пришлите описание <b>ответом на это сообщение</b> "
                f"({DESC_MIN_LEN}–{DESC_MAX_LEN} симв.).\n/cancel — отмена",
                chat_id=chat_id,
                reply_markup={"force_reply": True, "selective": False},
            )
            return

        ai_text = str(ai["text"]).strip()
        state["awaiting"] = "ai_description_choice"
        state["ai_description"] = ai_text
        state["custom_description"] = None
        self.pending[chat_id] = state

        preview = self.bot._escape_html(ai_text)
        if len(preview) > 3200:
            preview = preview[:3200] + "…"
        self.bot.send_message(
            f"Свой отклик — описание для: <b>{self.bot._escape_html(str(name))}</b>\n"
            f"Цена: <b>{price}</b> ₽ · Срок: <b>{days}</b> дн.\n"
            f"Длина: {len(ai_text)} симв.\n\n"
            f"{preview}",
            chat_id=chat_id,
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Использовать", "callback_data": f"desc_use:{project_id}"},
                        {"text": "Напишу сам", "callback_data": f"desc_edit:{project_id}"},
                    ],
                ]
            },
        )

    def _handle_callback(self, cq: Dict[str, Any]) -> None:
        data = (cq.get("data") or "").strip()
        chat = cq.get("message", {}).get("chat", {})
        chat_id = str(chat.get("id", ""))
        message_id = cq.get("message", {}).get("message_id")
        cq_id = cq.get("id", "")

        if not self._allowed_chat(chat_id):
            self.bot.answer_callback_query(cq_id, "Чат не разрешён")
            return

        if data == "offer_cancel":
            state = self.pending.pop(chat_id, None)
            self.bot.answer_callback_query(cq_id, "Отменено")
            if message_id:
                self.bot.edit_message_reply_markup(chat_id, message_id, None)
            if state and state.get("project_id"):
                self._restore_announcement_buttons(str(state["project_id"]), state)
            self.bot.send_message("Отклик отменён.", chat_id=chat_id)
            return

        # --- Автоотклик: цена авто → выбор срока → шаблон ---
        auto_match = re.match(r"^offer_auto:(\d+)$", data) or re.match(r"^offer:(\d+)$", data)
        if auto_match:
            project_id = auto_match.group(1)
            project = self._load_project(project_id)
            if not project:
                self.bot.answer_callback_query(cq_id, "Проект не найден в БД", show_alert=True)
                return
            price = project_offer_price(project)
            if not price or price <= 0:
                self.bot.answer_callback_query(cq_id, "Не удалось определить цену", show_alert=True)
                return
            state = {
                "project_id": project_id,
                "project": project,
                "price": price,
                "days": None,
                "mode": "auto",
                "awaiting": "deadline",
                "custom_description": None,
            }
            self._bind_announcement_from_callback(state, project_id, cq)
            self.pending[chat_id] = state
            low, high = project_budget_bounds(project)
            budget_hint = ""
            if low and high and low != high:
                budget_hint = f" (среднее из {low}–{high})"
            self.bot.answer_callback_query(cq_id, "Автоотклик")
            name = project.get("name", project_id)
            self.bot.send_message(
                f"Автоотклик: <b>{self.bot._escape_html(str(name))}</b>\n"
                f"Цена: <b>{price}</b> ₽{budget_hint}\n"
                f"Текст: шаблон\n\n"
                f"Выберите срок выполнения:",
                chat_id=chat_id,
                reply_markup=self._deadline_keyboard(project_id),
            )
            return

        # --- Свой отклик: цена → срок → описание ---
        custom_match = re.match(r"^offer_custom:(\d+)$", data) or re.match(r"^offer_desc:(\d+)$", data)
        if custom_match:
            project_id = custom_match.group(1)
            project = self._load_project(project_id)
            if not project:
                self.bot.answer_callback_query(cq_id, "Проект не найден в БД", show_alert=True)
                return
            low, high = project_budget_bounds(project)
            floor = project_min_offer_price(project)
            state = {
                "project_id": project_id,
                "project": project,
                "price": None,
                "days": None,
                "mode": "custom",
                "awaiting": "custom_price",
                "custom_description": None,
            }
            self._bind_announcement_from_callback(state, project_id, cq)
            self.pending[chat_id] = state
            self.bot.answer_callback_query(cq_id, "Свой отклик")
            name = project.get("name", project_id)
            if floor and high:
                hint = (
                    f"\nБюджет: <b>{low or floor}</b>–<b>{high}</b> ₽"
                    f"\nКнопки: от <b>{floor}</b> (−20%) до <b>{high}</b> ₽."
                )
            elif high:
                hint = f"\nВ объявлении до <b>{high}</b> ₽."
            else:
                hint = ""
            self.bot.send_message(
                f"Свой отклик: <b>{self.bot._escape_html(str(name))}</b>{hint}\n\n"
                f"1/3 — выберите цену:",
                chat_id=chat_id,
                reply_markup=self._price_keyboard(project_id, project),
            )
            return

        price_match = re.match(r"^price:(\d+):(\d+)$", data)
        if price_match:
            project_id, price_s = price_match.group(1), price_match.group(2)
            price = int(price_s)
            state = self.pending.get(chat_id) or {}
            project = state.get("project") or self._load_project(project_id)
            if not project:
                self.bot.answer_callback_query(cq_id, "Проект не найден", show_alert=True)
                return
            max_price = project_max_price(project)
            if max_price and price > max_price:
                self.bot.answer_callback_query(
                    cq_id, f"Максимум {max_price} ₽ по объявлению", show_alert=True
                )
                return
            state.update(
                {
                    "project_id": project_id,
                    "project": project,
                    "price": price,
                    "mode": "custom",
                    "awaiting": "deadline",
                }
            )
            self.pending[chat_id] = state
            self.bot.answer_callback_query(cq_id, f"{price} ₽")
            if message_id:
                self.bot.edit_message_reply_markup(chat_id, message_id, None)
            self.bot.send_message("2/3 — выберите срок:", chat_id=chat_id)
            self._ask_deadline(chat_id, project_id, price, project.get("name", project_id))
            return

        price_custom_match = re.match(r"^price_custom:(\d+)$", data)
        if price_custom_match:
            project_id = price_custom_match.group(1)
            state = self.pending.get(chat_id) or {}
            project = state.get("project") or self._load_project(project_id)
            if not project:
                self.bot.answer_callback_query(cq_id, "Проект не найден", show_alert=True)
                return
            state.update(
                {
                    "project_id": project_id,
                    "project": project,
                    "price": None,
                    "mode": "custom",
                    "awaiting": "custom_price",
                }
            )
            self.pending[chat_id] = state
            self.bot.answer_callback_query(cq_id)
            self.bot.send_message(
                "Введите цену числом <b>ответом на это сообщение</b>, например: 5000\n"
                "/cancel — отмена",
                chat_id=chat_id,
                reply_markup={"force_reply": True, "selective": False},
            )
            return

        deadline_custom_match = re.match(r"^deadline_custom:(\d+)$", data)
        if deadline_custom_match:
            project_id = deadline_custom_match.group(1)
            state = self.pending.get(chat_id)
            if not state or state.get("project_id") != project_id:
                self.bot.answer_callback_query(
                    cq_id, "Сессия устарела, нажмите отклик снова", show_alert=True
                )
                return
            state["awaiting"] = "deadline_text"
            self.bot.answer_callback_query(cq_id)
            self.bot.send_message(
                "Напишите срок в днях числом (например 4).\n/cancel — отмена",
                chat_id=chat_id,
                reply_markup={"force_reply": True, "selective": True},
            )
            return

        deadline_match = re.match(r"^deadline:(\d+):(\d+)$", data)
        if deadline_match:
            project_id, days_s = deadline_match.group(1), deadline_match.group(2)
            days = int(days_s)
            state = self.pending.get(chat_id) or {}
            if state.get("project_id") != project_id:
                self.bot.answer_callback_query(cq_id, "Сессия устарела", show_alert=True)
                return
            self.bot.answer_callback_query(cq_id, f"Срок: {days} дн.")
            if message_id:
                self.bot.edit_message_reply_markup(chat_id, message_id, None)

            state["days"] = days
            state["awaiting"] = None
            self.pending[chat_id] = state
            if state.get("mode") == "custom":
                self.bot.send_message("3/3 — описание:", chat_id=chat_id)
                self._start_description_step(chat_id, project_id, state)
            else:
                # автоотклик: цена уже есть, текст — шаблон
                self.bot.send_message(
                    f"Срок: <b>{days}</b> дн. Отправляем отклик…",
                    chat_id=chat_id,
                )
                self._submit(chat_id, project_id, days)
            return

        desc_use_match = re.match(r"^desc_use:(\d+)$", data)
        if desc_use_match:
            project_id = desc_use_match.group(1)
            state = self.pending.get(chat_id) or {}
            if state.get("project_id") != project_id:
                self.bot.answer_callback_query(cq_id, "Сессия устарела", show_alert=True)
                return
            ai_text = (state.get("ai_description") or "").strip()
            if not ai_text:
                self.bot.answer_callback_query(cq_id, "Нет текста ИИ", show_alert=True)
                return
            days = state.get("days")
            if not days:
                self.bot.answer_callback_query(cq_id, "Не выбран срок", show_alert=True)
                return
            state["custom_description"] = ai_text
            self.pending[chat_id] = state
            self.bot.answer_callback_query(cq_id, "Описание принято")
            if message_id:
                self.bot.edit_message_reply_markup(chat_id, message_id, None)
            self._submit(chat_id, project_id, int(days))
            return

        desc_edit_match = re.match(r"^desc_edit:(\d+)$", data)
        if desc_edit_match:
            project_id = desc_edit_match.group(1)
            state = self.pending.get(chat_id) or {}
            if state.get("project_id") != project_id:
                self.bot.answer_callback_query(cq_id, "Сессия устарела", show_alert=True)
                return
            state["awaiting"] = "custom_description"
            self.pending[chat_id] = state
            self.bot.answer_callback_query(cq_id)
            if message_id:
                self.bot.edit_message_reply_markup(chat_id, message_id, None)
            self.bot.send_message(
                f"Пришлите текст отклика <b>ответом на это сообщение</b> "
                f"({DESC_MIN_LEN}–{DESC_MAX_LEN} симв.).\n/cancel — отмена",
                chat_id=chat_id,
                reply_markup={"force_reply": True, "selective": False},
            )
            return

        self.bot.answer_callback_query(cq_id)

    def _handle_message(self, message: Dict[str, Any]) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        if not self._allowed_chat(chat_id):
            return
        text = (message.get("text") or "").strip()
        if not text:
            return

        state = self.pending.get(chat_id)
        if not state:
            return

        if text.lower() in {"/cancel", "отмена", "cancel"}:
            cancelled = self.pending.pop(chat_id, None)
            if cancelled and cancelled.get("project_id"):
                self._restore_announcement_buttons(str(cancelled["project_id"]), cancelled)
            self.bot.send_message("Отклик отменён.", chat_id=chat_id)
            return

        awaiting = state.get("awaiting")

        if awaiting == "custom_price":
            price_raw = text.replace(" ", "").replace("\u00a0", "").replace(",", ".")
            price_raw = re.sub(r"[^\d.]", "", price_raw)
            if not re.fullmatch(r"\d+(\.\d+)?", price_raw):
                self.bot.send_message(
                    "Введите цену числом, например: 5000",
                    chat_id=chat_id,
                )
                return
            price = int(float(price_raw))
            if price < 1 or price > 10_000_000:
                self.bot.send_message("Цена должна быть от 1 до 10 000 000 ₽.", chat_id=chat_id)
                return
            project = state.get("project") or {}
            max_price = project_max_price(project)
            if max_price and price > max_price:
                self.bot.send_message(
                    f"Цена выше лимита объявления (макс. {max_price} ₽).",
                    chat_id=chat_id,
                )
                return
            state["price"] = price
            state["awaiting"] = "deadline"
            project_id = state["project_id"]
            name = project.get("name", project_id)
            self.bot.send_message("2/3 — выберите срок:", chat_id=chat_id)
            self._ask_deadline(chat_id, project_id, price, name)
            return

        if awaiting == "deadline_text":
            if not re.fullmatch(r"\d{1,3}", text):
                self.bot.send_message("Введите целое число дней, например: 5", chat_id=chat_id)
                return
            days = int(text)
            if days < 1 or days > 90:
                self.bot.send_message("Срок должен быть от 1 до 90 дней.", chat_id=chat_id)
                return
            state["days"] = days
            state["awaiting"] = None
            project_id = state["project_id"]
            self.pending[chat_id] = state
            if state.get("mode") == "custom":
                self.bot.send_message("3/3 — описание:", chat_id=chat_id)
                self._start_description_step(chat_id, project_id, state)
            else:
                self.bot.send_message(
                    f"Срок: <b>{days}</b> дн. Отправляем отклик…",
                    chat_id=chat_id,
                )
                self._submit(chat_id, project_id, days)
            return

        if awaiting == "custom_description":
            desc = text.strip()
            if len(desc) < DESC_MIN_LEN:
                self.bot.send_message(
                    f"Слишком коротко ({len(desc)} симв.). Минимум {DESC_MIN_LEN}.",
                    chat_id=chat_id,
                )
                return
            if len(desc) > DESC_MAX_LEN:
                self.bot.send_message(
                    f"Слишком длинно ({len(desc)} симв.). Максимум {DESC_MAX_LEN}.",
                    chat_id=chat_id,
                )
                return
            days = state.get("days")
            if not days:
                self.bot.send_message("Не выбран срок. Начните «Свой отклик» заново.", chat_id=chat_id)
                return
            state["custom_description"] = desc
            self.pending[chat_id] = state
            self.bot.send_message(f"Описание принято ({len(desc)} симв.).", chat_id=chat_id)
            self._submit(chat_id, state["project_id"], int(days))
            return

    def _submit(self, chat_id: str, project_id: str, days: int) -> None:
        state = self.pending.pop(chat_id, None)
        project = (state or {}).get("project") or self._load_project(project_id)
        if not project:
            self.bot.send_message("Проект не найден.", chat_id=chat_id)
            self._restore_announcement_buttons(project_id, state)
            return

        price = (state or {}).get("price") or project_offer_price(project)
        if not price or price <= 0:
            self.bot.send_message(
                "Не удалось определить цену проекта. Отклик не отправлен.",
                chat_id=chat_id,
            )
            self._restore_announcement_buttons(project_id, state)
            return

        max_price = project_max_price(project)
        if max_price and price > max_price:
            self.bot.send_message(
                f"Цена {price} ₽ выше лимита объявления ({max_price} ₽). Отклик не отправлен.",
                chat_id=chat_id,
            )
            self._restore_announcement_buttons(project_id, state)
            return

        custom_description = (state or {}).get("custom_description")
        if custom_description:
            response_text = str(custom_description).strip()
            desc_note = "своё описание"
        else:
            response_text = build_response_text(
                project.get("name", ""),
                project.get("description", "") or "",
            )
            desc_note = "шаблон"

        self.bot.send_message(
            f"Отправляю отклик на проект {project_id}...\n"
            f"Цена: {price} ₽, срок: {days} дн., текст: {desc_note}",
            chat_id=chat_id,
        )

        result = submit_offer(
            project_id,
            response_text,
            price,
            days,
            order_name=project.get("name", "") or f"Проект {project_id}",
        )
        if result.get("ok"):
            self._mark_announcement_done(project_id, state)
            self.bot.send_message(
                f"✅ Отклик отправлен.\n"
                f"Проект: {project_id}\n"
                f"Цена: {price} ₽\n"
                f"Срок: {days} дн.\n"
                f"Текст: {desc_note}\n"
                f"<a href=\"https://kwork.ru/projects/{project_id}\">Открыть проект</a>",
                chat_id=chat_id,
            )
        else:
            self._restore_announcement_buttons(project_id, state)
            self.bot.send_message(
                f"Не удалось отправить отклик.\n"
                f"Ошибка: {self.bot._escape_html(str(result.get('error', 'unknown')))}",
                chat_id=chat_id,
            )

    def _load_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        if project_id in self.project_cache:
            return self.project_cache[project_id]
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM projects WHERE id = ?", (int(project_id),))
            row = cur.fetchone()
            if not row:
                return None
            project = dict(row)
            cur.execute(
                """
                SELECT b.* FROM buyers b
                JOIN project_buyers pb ON b.user_id = pb.buyer_user_id
                WHERE pb.project_id = ?
                """,
                (int(project_id),),
            )
            buyer = cur.fetchone()
            if buyer:
                project["buyer"] = dict(buyer)
            self.project_cache[project_id] = project
            return project
        except Exception as exc:
            print(f"❌ Ошибка чтения проекта {project_id}: {exc}")
            return None
        finally:
            if conn is not None:
                conn.close()
