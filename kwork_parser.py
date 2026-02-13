import requests
from bs4 import BeautifulSoup
import json
import time
from typing import List, Dict, Optional
import re
from database_manager import DatabaseManager
from telegram_bot import TelegramBot
import config


class KworkParser:
    """Парсер проектов с сайта kwork.ru с сохранением в БД и отправкой в Telegram"""
    
    def __init__(self, db_path: str = "kwork_projects.db", use_telegram: bool = True):
        self.base_url = "https://kwork.ru"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        self.db = DatabaseManager(db_path)
        
        # Инициализация Telegram бота
        self.use_telegram = use_telegram
        self.telegram = None
        if use_telegram:
            self._init_telegram()
    
    def _init_telegram(self):
        """Инициализация Telegram бота"""
        try:
            if not hasattr(config, 'TELEGRAM_BOT_TOKEN') or not hasattr(config, 'TELEGRAM_CHAT_ID'):
                print("⚠️  Настройки Telegram не найдены в config.py")
                self.use_telegram = False
                return
            
            if config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
                print("⚠️  Укажите TELEGRAM_BOT_TOKEN в config.py")
                self.use_telegram = False
                return
            
            if config.TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE":
                print("⚠️  Укажите TELEGRAM_CHAT_ID в config.py")
                self.use_telegram = False
                return
            
            self.telegram = TelegramBot(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
            
            # Проверка подключения
            if self.telegram.test_connection():
                print("✓ Telegram бот подключен")
            else:
                print("⚠️  Не удалось подключиться к Telegram боту")
                self.use_telegram = False
                
        except Exception as e:
            print(f"❌ Ошибка инициализации Telegram: {e}")
            self.use_telegram = False
    
    def parse_page(self, page: int = 1) -> List[Dict]:
        """
        Парсит страницу с проектами
        
        Args:
            page: номер страницы для парсинга
            
        Returns:
            Список словарей с данными о проектах
        """
        url = f"{self.base_url}/projects?c=11&page={page}"
        
        try:
            print(f"\n{'='*60}")
            print(f"📄 Запрос к URL: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            print(f"✓ Статус ответа: {response.status_code}")
            
            # Извлекаем данные из JavaScript
            projects = self._extract_projects_from_js(response.text)
            
            print(f"✓ Найдено проектов на странице: {len(projects)}")
            print(f"{'='*60}\n")
            
            return projects
            
        except requests.RequestException as e:
            print(f"❌ Ошибка при запросе страницы {page}: {e}")
            return []
    
    def _extract_projects_from_js(self, html: str) -> List[Dict]:
        """
        Извлекает данные о проектах из JavaScript переменной window.stateData
        
        Args:
            html: HTML-код страницы
            
        Returns:
            Список проектов
        """
        try:
            # Ищем window.stateData в коде страницы
            pattern = r'window\.stateData\s*=\s*({.*?});'
            match = re.search(pattern, html, re.DOTALL)
            
            if not match:
                print("⚠️  Не найдена переменная window.stateData")
                return []
            
            # Парсим JSON
            state_data = json.loads(match.group(1))
            
            # Проверяем наличие данных о проектах
            if 'wantsListData' not in state_data:
                print("⚠️  Нет данных wantsListData в stateData")
                return []
            
            wants_list = state_data['wantsListData']
            
            # Извлекаем список проектов
            if 'pagination' in wants_list and 'data' in wants_list['pagination']:
                projects_raw = wants_list['pagination']['data']
            elif 'wants' in wants_list:
                projects_raw = wants_list['wants']
            else:
                print("⚠️  Не найден список проектов в данных")
                return []
            
            # Преобразуем в удобный формат
            projects = []
            for proj in projects_raw:
                project = self._parse_project_data(proj)
                if project:
                    projects.append(project)
            
            return projects
            
        except json.JSONDecodeError as e:
            print(f"❌ Ошибка парсинга JSON: {e}")
            return []
        except Exception as e:
            print(f"❌ Ошибка извлечения данных: {e}")
            return []
    
    def _parse_project_data(self, data: Dict) -> Optional[Dict]:
        """
        Преобразует сырые данные проекта в удобный формат
        
        Args:
            data: Словарь с данными проекта
            
        Returns:
            Очищенный словарь с данными проекта
        """
        try:
            project = {
                'id': data.get('id'),
                'name': data.get('name', ''),
                'url': f"{self.base_url}/projects/{data.get('id', '')}",
                'description': data.get('description', ''),
                'price_limit': data.get('priceLimit', ''),
                'possible_price_limit': data.get('possiblePriceLimit', ''),
                'category_id': data.get('category_id', ''),
                'status': data.get('status', ''),
                'time_left': data.get('timeLeft', ''),
                'offers_count': data.get('views_dirty', 0),
                'date_create': data.get('date_create', ''),
                'date_active': data.get('date_active', ''),
                'date_expire': data.get('date_expire', ''),
                'kwork_count': data.get('kwork_count', 0),
                'is_higher_price': data.get('isHigherPrice', False),
            }
            
            # Данные о покупателе
            if 'user' in data:
                user = data['user']
                project['buyer'] = {
                    'username': user.get('username', ''),
                    'user_id': user.get('USERID', ''),
                    'profile_url': f"{self.base_url}/user/{user.get('username', '')}",
                    'avatar': user.get('profilepicture', ''),
                }
                
                # Статистика покупателя
                if 'data' in user:
                    user_data = user['data']
                    project['buyer']['wants_count'] = user_data.get('wants_count', '0')
                    project['buyer']['hired_percent'] = user_data.get('wants_hired_percent', '0')
            
            return project
            
        except Exception as e:
            print(f"❌ Ошибка обработки проекта: {e}")
            return None
    
    def parse_and_save(self, start_page: int = 1, end_page: int = 5, delay: float = 2.0) -> Dict:
        """
        Парсит страницы, сохраняет в БД и отправляет новые проекты в Telegram
        
        Args:
            start_page: начальная страница
            end_page: конечная страница
            delay: задержка между запросами в секундах
            
        Returns:
            Статистика парсинга
        """
        # Уведомление о начале парсинга отключено
        
        # Подключаемся к БД
        self.db.connect()
        self.db.init_database()
        
        all_new_projects = []
        total_inserted = 0
        total_skipped = 0
        
        for page in range(start_page, end_page + 1):
            print(f"\n{'#'*60}")
            print(f"📄 СТРАНИЦА {page}")
            print(f"{'#'*60}")
            
            projects = self.parse_page(page)
            
            if projects:
                # Получаем ID существующих проектов
                project_ids = [p.get('id') for p in projects if p.get('id')]
                existing_ids = self.db.get_existing_project_ids(project_ids)
                
                # Разделяем на новые и существующие
                new_projects = []
                for proj in projects:
                    proj_id = proj.get('id')
                    if not proj_id:
                        continue
                    
                    if proj_id in existing_ids:
                        total_skipped += 1
                        print(f"⊘ Проект ID {proj_id} уже существует, пропускаем")
                    else:
                        new_projects.append(proj)
                        total_inserted += 1
                        print(f"✓ Добавлен проект ID {proj_id}: {proj.get('name', '')[:50]}")
                
                # Сохраняем только новые в БД
                if new_projects:
                    for proj in new_projects:
                        self.db.insert_project(proj)
                    self.db.conn.commit()
                    all_new_projects.extend(new_projects)
                    
                    # Отправляем в Telegram
                    if self.use_telegram:
                        self._send_to_telegram(new_projects)
            
            if page < end_page:
                print(f"\n⏳ Ожидание {delay} сек. перед следующей страницей...")
                time.sleep(delay)
        
        # Получаем статистику БД
        db_stats = self.db.get_statistics()
        
        # Сохраняем только НОВЫЕ проекты в JSON
        if all_new_projects:
            self._save_new_projects_to_json(all_new_projects, "new_projects.json")
        
        self.db.disconnect()
        
        stats = {
            'total_parsed': total_inserted + total_skipped,
            'new_projects': total_inserted,
            'skipped_existing': total_skipped,
            'db_total_projects': db_stats.get('total_projects', 0),
            'db_total_buyers': db_stats.get('total_buyers', 0)
        }
        
        # Отправка статистики в Telegram отключена
        
        return stats
    
    def _send_to_telegram(self, projects: List[Dict]):
        """
        Отправка проектов в Telegram
        
        Args:
            projects: список новых проектов
        """
        if not self.use_telegram or not self.telegram:
            return
        
        try:
            # Проверяем настройку индивидуальной отправки
            send_individual = getattr(config, 'SEND_INDIVIDUAL_PROJECTS', False)
            
            if send_individual:
                # Отправляем каждый проект отдельным сообщением
                for project in projects:
                    self.telegram.send_project(project)
                    time.sleep(0.5)  # Небольшая задержка
            else:
                # Отправляем пакетами
                batch_size = getattr(config, 'PROJECTS_PER_MESSAGE', 5)
                self.telegram.send_projects_batch(projects, batch_size)
                
        except Exception as e:
            print(f"❌ Ошибка отправки в Telegram: {e}")
    
    def _save_new_projects_to_json(self, projects: List[Dict], filename: str):
        """
        Сохраняет только новые проекты в JSON
        
        Args:
            projects: список новых проектов
            filename: имя файла
        """
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
        print(f"\n✓ Новые проекты ({len(projects)} шт.) сохранены в {filename}")
    
    def export_all_from_db(self, filename: str = "all_projects.json"):
        """
        Экспортирует все проекты из БД в JSON
        
        Args:
            filename: имя выходного файла
        """
        self.db.connect()
        count = self.db.export_new_projects_to_json(filename)
        self.db.disconnect()
        return count


def main():
    """Основная функция для запуска парсера"""
    print("="*60)
    print("🚀 ПАРСЕР ПРОЕКТОВ KWORK.RU")
    print("📱 С ОТПРАВКОЙ В TELEGRAM")
    print("="*60)
    
    # Создаем парсер
    parser = KworkParser("kwork_projects.db", use_telegram=True)
    
    # Получаем настройки из config
    run_mode = getattr(config, 'RUN_MODE', 'once')
    interval_minutes = getattr(config, 'CHECK_INTERVAL_MINUTES', 10)
    start_page = getattr(config, 'START_PAGE', 1)
    end_page = getattr(config, 'END_PAGE', 3)
    page_delay = getattr(config, 'PAGE_DELAY', 2.0)
    
    if run_mode == 'loop':
        print(f"\n🔄 РЕЖИМ: Непрерывная работа")
        print(f"⏱️  Интервал проверки: {interval_minutes} минут")
        print(f"📄 Страницы: {start_page} - {end_page}")
        print(f"⌛ Задержка между страницами: {page_delay} сек")
        print(f"\n⚠️  Для остановки нажмите Ctrl+C")
        print("="*60)
        
        run_count = 0
        
        while True:
            try:
                run_count += 1
                print(f"\n{'🔄'*20}")
                print(f"▶️  ЗАПУСК #{run_count}")
                print(f"🕐 Время: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'🔄'*20}\n")
                
                # Парсим и сохраняем
                stats = parser.parse_and_save(
                    start_page=start_page, 
                    end_page=end_page, 
                    delay=page_delay
                )
                
                # Выводим статистику
                print("\n" + "="*60)
                print(f"📊 СТАТИСТИКА ЗАПУСКА #{run_count}")
                print("="*60)
                print(f"🔍 Всего спарсено: {stats['total_parsed']}")
                print(f"✨ Новых проектов: {stats['new_projects']}")
                print(f"⊘ Пропущено: {stats['skipped_existing']}")
                print(f"💾 Всего в БД: {stats['db_total_projects']}")
                print("="*60)
                
                # Ожидание перед следующим запуском
                interval_seconds = interval_minutes * 60
                next_run = time.strftime('%H:%M:%S', time.localtime(time.time() + interval_seconds))
                
                print(f"\n⏳ Ожидание {interval_minutes} минут до следующей проверки...")
                print(f"⏰ Следующий запуск в: {next_run}")
                print(f"💤 Нажмите Ctrl+C для остановки\n")
                
                time.sleep(interval_seconds)
                
            except KeyboardInterrupt:
                print("\n\n" + "="*60)
                print("⛔ ОСТАНОВКА ПАРСЕРА")
                print("="*60)
                print(f"✅ Всего запусков: {run_count}")
                print(f"📊 Работа завершена корректно")
                print("="*60 + "\n")
                break
                
            except Exception as e:
                print(f"\n❌ Ошибка в цикле: {e}")
                print(f"⏳ Повторная попытка через {interval_minutes} минут...\n")
                time.sleep(interval_minutes * 60)
    
    else:
        # Режим одноразового запуска
        print(f"\n▶️  РЕЖИМ: Одноразовый запуск")
        print(f"📄 Страницы: {start_page} - {end_page}")
        print("="*60 + "\n")
        
        stats = parser.parse_and_save(
            start_page=start_page, 
            end_page=end_page, 
            delay=page_delay
        )
        
        # Выводим итоговую статистику
        print("\n" + "="*60)
        print("📊 ИТОГОВАЯ СТАТИСТИКА")
        print("="*60)
        print(f"🔍 Всего спарсено проектов: {stats['total_parsed']}")
        print(f"✨ Новых проектов добавлено: {stats['new_projects']}")
        print(f"⊘ Пропущено (уже в БД): {stats['skipped_existing']}")
        print(f"💾 Всего проектов в БД: {stats['db_total_projects']}")
        print(f"👥 Всего покупателей в БД: {stats['db_total_buyers']}")
        print("="*60)


if __name__ == "__main__":
    main()