import requests
from typing import List, Dict, Optional
import time


class TelegramBot:
    """Класс для работы с Telegram Bot API"""
    
    def __init__(self, bot_token: str, chat_id: str):
        """
        Инициализация бота
        
        Args:
            bot_token: токен бота от @BotFather
            chat_id: ID чата для отправки сообщений
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Отправка текстового сообщения
        
        Args:
            text: текст сообщения
            parse_mode: режим форматирования (HTML или Markdown)
            
        Returns:
            True если успешно, False иначе
        """
        url = f"{self.base_url}/sendMessage"
        
        # Telegram ограничивает длину сообщения в 4096 символов
        if len(text) > 4096:
            # Разбиваем на части
            return self._send_long_message(text, parse_mode)
        
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"❌ Ошибка отправки сообщения в Telegram: {e}")
            return False
    
    def _send_long_message(self, text: str, parse_mode: str) -> bool:
        """
        Отправка длинного сообщения частями
        
        Args:
            text: текст сообщения
            parse_mode: режим форматирования
            
        Returns:
            True если все части отправлены успешно
        """
        max_length = 4000  # Оставляем запас
        parts = []
        
        # Разбиваем текст на части
        while text:
            if len(text) <= max_length:
                parts.append(text)
                break
            
            # Ищем последний перенос строки в пределах max_length
            split_pos = text.rfind('\n', 0, max_length)
            if split_pos == -1:
                split_pos = max_length
            
            parts.append(text[:split_pos])
            text = text[split_pos:].lstrip()
        
        # Отправляем части
        for i, part in enumerate(parts):
            if i > 0:
                time.sleep(1)  # Задержка между сообщениями
            
            if not self.send_message(part, parse_mode):
                return False
        
        return True
    
    def send_project(self, project: Dict) -> bool:
        """
        Отправка информации о проекте
        
        Args:
            project: словарь с данными о проекте
            
        Returns:
            True если успешно
        """
        message = self._format_project_message(project)
        return self.send_message(message)
    
    def send_projects_batch(self, projects: List[Dict], batch_size: int = 5) -> int:
        """
        Отправка списка проектов (группировка по batch_size)
        
        Args:
            projects: список проектов
            batch_size: количество проектов в одном сообщении
            
        Returns:
            Количество успешно отправленных проектов
        """
        sent_count = 0
        
        for i in range(0, len(projects), batch_size):
            batch = projects[i:i + batch_size]
            message = self._format_projects_batch(batch, i + 1)
            
            if self.send_message(message):
                sent_count += len(batch)
                print(f"✓ Отправлено в Telegram: {len(batch)} проектов")
            else:
                print(f"❌ Ошибка отправки пакета проектов {i + 1}")
            
            # Задержка между пакетами
            if i + batch_size < len(projects):
                time.sleep(1)
        
        return sent_count
    
    def _format_project_message(self, project: Dict) -> str:
        """
        Форматирование сообщения о проекте (БЕЗ эмодзи)
        
        Args:
            project: данные проекта
            
        Returns:
            Отформатированное сообщение
        """
        name = self._escape_html(project.get('name', 'Без названия'))
        description = self._escape_html(project.get('description', 'Нет описания'))
        
        # Обрезаем описание если слишком длинное
        if len(description) > 500:
            description = description[:497] + "..."
        
        price_limit = project.get('price_limit', 'N/A')
        possible_price = project.get('possible_price_limit', 'N/A')
        time_left = project.get('time_left', 'N/A')
        url = project.get('url', '')
        
        # Информация о покупателе
        buyer_info = ""
        if 'buyer' in project and project['buyer']:
            buyer = project['buyer']
            username = self._escape_html(buyer.get('username', 'N/A'))
            wants_count = buyer.get('wants_count', '0')
            hired_percent = buyer.get('hired_percent', '0')
            buyer_info = f"""
<b>Заказчик:</b> {username}
Проектов: {wants_count} | Нанято: {hired_percent}%"""
        
        message = f"""
<b>{name}</b>

<b>Бюджет:</b> {price_limit} - {possible_price} ₽
<b>Осталось:</b> {time_left}
{buyer_info}

<b>Описание:</b>
{description}

<a href="{url}">Перейти к проекту</a>
━━━━━━━━━━━━━━━━━━━━
"""
        return message.strip()
    
    def _format_projects_batch(self, projects: List[Dict], start_num: int) -> str:
        """
        Форматирование группы проектов в одно сообщение (БЕЗ эмодзи)
        
        Args:
            projects: список проектов
            start_num: начальный номер
            
        Returns:
            Отформатированное сообщение
        """
        messages = [f"<b>Новые проекты ({start_num}-{start_num + len(projects) - 1}):</b>\n"]
        
        for i, project in enumerate(projects, start_num):
            name = self._escape_html(project.get('name', 'Без названия'))
            price_limit = project.get('price_limit', 'N/A')
            possible_price = project.get('possible_price_limit', 'N/A')
            url = project.get('url', '')
            
            msg = f"""
{i}. <b>{name}</b>
   {price_limit} - {possible_price} ₽
   <a href="{url}">Открыть</a>
"""
            messages.append(msg)
        
        return "".join(messages)
    
    def _escape_html(self, text: str) -> str:
        """
        Экранирование HTML символов для Telegram
        
        Args:
            text: исходный текст
            
        Returns:
            Экранированный текст
        """
        if not text:
            return ""
        
        text = str(text)
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        return text
    
    def test_connection(self) -> bool:
        """
        Проверка подключения к боту
        
        Returns:
            True если подключение успешно
        """
        url = f"{self.base_url}/getMe"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('ok'):
                bot_info = data.get('result', {})
                bot_name = bot_info.get('first_name', 'Unknown')
                print(f"✓ Подключение к боту успешно: @{bot_info.get('username', bot_name)}")
                return True
            else:
                print(f"❌ Ошибка бота: {data.get('description', 'Unknown error')}")
                return False
                
        except requests.RequestException as e:
            print(f"❌ Ошибка подключения к Telegram: {e}")
            return False