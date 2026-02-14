import sqlite3
import json
from typing import List, Dict, Optional
from datetime import datetime


class DatabaseManager:
    """Менеджер для работы с базой данных SQLite"""
    
    def __init__(self, db_path: str = "kwork_projects.db"):
        """
        Инициализация менеджера БД
        
        Args:
            db_path: путь к файлу базы данных
        """
        self.db_path = db_path
        self.conn = None
        self.cursor = None
    
    def connect(self):
        """Подключение к базе данных"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Для получения результатов как словарей
        self.cursor = self.conn.cursor()
        print(f"✓ Подключено к базе данных: {self.db_path}")
    
    def disconnect(self):
        """Отключение от базы данных"""
        if self.conn:
            self.conn.close()
            print("✓ Отключено от базы данных")
    
    def init_database(self):
        """Инициализация базы данных (создание таблиц)"""
        
        # Создание таблицы проектов
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                description TEXT,
                price_limit TEXT,
                possible_price_limit TEXT,
                category_id TEXT,
                status TEXT,
                time_left TEXT,
                offers_count INTEGER DEFAULT 0,
                date_create TEXT,
                date_active TEXT,
                date_expire TEXT,
                kwork_count INTEGER DEFAULT 0,
                is_higher_price BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создание таблицы покупателей
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS buyers (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                profile_url TEXT,
                avatar TEXT,
                wants_count TEXT,
                hired_percent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Связь проектов с покупателями
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_buyers (
                project_id INTEGER,
                buyer_user_id TEXT,
                PRIMARY KEY (project_id, buyer_user_id),
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (buyer_user_id) REFERENCES buyers(user_id)
            )
        """)
        
        # Индексы
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_projects_date_create 
            ON projects(date_create)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_projects_status 
            ON projects(status)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_buyers_username 
            ON buyers(username)
        """)
        
        self.conn.commit()
        print("✓ База данных инициализирована")
    
    def project_exists(self, project_id: int) -> bool:
        """
        Проверка существования проекта в БД
        
        Args:
            project_id: ID проекта
            
        Returns:
            True если проект существует, False иначе
        """
        self.cursor.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        return self.cursor.fetchone() is not None
    
    def get_existing_project_ids(self, project_ids: List[int]) -> set:
        """
        Получить список ID проектов, которые уже есть в БД
        
        Args:
            project_ids: список ID проектов для проверки
            
        Returns:
            Множество ID существующих проектов
        """
        if not project_ids:
            return set()
        
        placeholders = ','.join('?' * len(project_ids))
        query = f"SELECT id FROM projects WHERE id IN ({placeholders})"
        self.cursor.execute(query, project_ids)
        
        return {row['id'] for row in self.cursor.fetchall()}
    
    def insert_buyer(self, buyer_data: Dict):
        """
        Вставка или обновление данных о покупателе
        
        Args:
            buyer_data: словарь с данными о покупателе
        """
        self.cursor.execute("""
            INSERT OR REPLACE INTO buyers 
            (user_id, username, profile_url, avatar, wants_count, hired_percent, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            buyer_data.get('user_id', ''),
            buyer_data.get('username', ''),
            buyer_data.get('profile_url', ''),
            buyer_data.get('avatar', ''),
            buyer_data.get('wants_count', ''),
            buyer_data.get('hired_percent', '')
        ))
    
    def insert_project(self, project: Dict):
        """
        Вставка проекта в БД
        
        Args:
            project: словарь с данными о проекте
        """
        self.cursor.execute("""
            INSERT OR REPLACE INTO projects 
            (id, name, url, description, price_limit, possible_price_limit, 
             category_id, status, time_left, offers_count, date_create, 
             date_active, date_expire, kwork_count, is_higher_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            project.get('id'),
            project.get('name', ''),
            project.get('url', ''),
            project.get('description', ''),
            project.get('price_limit', ''),
            project.get('possible_price_limit', ''),
            project.get('category_id', ''),
            project.get('status', ''),
            project.get('time_left', ''),
            project.get('offers_count', 0),
            project.get('date_create', ''),
            project.get('date_active', ''),
            project.get('date_expire', ''),
            project.get('kwork_count', 0),
            project.get('is_higher_price', False)
        ))
        
        # Вставка покупателя если есть
        if 'buyer' in project and project['buyer']:
            self.insert_buyer(project['buyer'])
            
            # Связь проекта с покупателем
            buyer_user_id = project['buyer'].get('user_id', '')
            if buyer_user_id:
                self.cursor.execute("""
                    INSERT OR IGNORE INTO project_buyers 
                    (project_id, buyer_user_id)
                    VALUES (?, ?)
                """, (project.get('id'), buyer_user_id))
    
    def insert_projects(self, projects: List[Dict]) -> Dict[str, int]:
        """
        Вставка списка проектов в БД
        
        Args:
            projects: список проектов
            
        Returns:
            Статистика: {'inserted': количество новых, 'skipped': количество пропущенных}
        """
        inserted = 0
        skipped = 0
        
        for project in projects:
            project_id = project.get('id')
            if not project_id:
                continue
            
            if self.project_exists(project_id):
                skipped += 1
                print(f"⊘ Проект ID {project_id} уже существует, пропускаем")
            else:
                self.insert_project(project)
                inserted += 1
                print(f"✓ Добавлен проект ID {project_id}: {project.get('name', 'Без названия')[:50]}")
        
        self.conn.commit()
        
        return {'inserted': inserted, 'skipped': skipped}
    
    def get_all_projects(self) -> List[Dict]:
        """
        Получить все проекты из БД
        
        Returns:
            Список всех проектов
        """
        self.cursor.execute("SELECT * FROM projects")
        rows = self.cursor.fetchall()
        
        projects = []
        for row in rows:
            project = dict(row)
            
            # Получаем данные о покупателе
            self.cursor.execute("""
                SELECT b.* FROM buyers b
                JOIN project_buyers pb ON b.user_id = pb.buyer_user_id
                WHERE pb.project_id = ?
            """, (project['id'],))
            
            buyer_row = self.cursor.fetchone()
            if buyer_row:
                project['buyer'] = dict(buyer_row)
            
            projects.append(project)
        
        return projects
    
    def get_statistics(self) -> Dict:
        """
        Получить статистику по базе данных
        
        Returns:
            Словарь со статистикой
        """
        stats = {}
        
        # Количество проектов
        self.cursor.execute("SELECT COUNT(*) as count FROM projects")
        stats['total_projects'] = self.cursor.fetchone()['count']
        
        # Количество покупателей
        self.cursor.execute("SELECT COUNT(*) as count FROM buyers")
        stats['total_buyers'] = self.cursor.fetchone()['count']
        
        # Последний добавленный проект
        self.cursor.execute("SELECT name, created_at FROM projects ORDER BY created_at DESC LIMIT 1")
        last_project = self.cursor.fetchone()
        if last_project:
            stats['last_project'] = dict(last_project)
        
        return stats
    
    def export_new_projects_to_json(self, output_file: str = "new_projects.json") -> int:
        """
        Экспорт всех проектов в JSON файл
        
        Args:
            output_file: имя выходного файла
            
        Returns:
            Количество экспортированных проектов
        """
        projects = self.get_all_projects()
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
        
        print(f"✓ Экспортировано {len(projects)} проектов в {output_file}")
        return len(projects)

    def clear_projects(self):
        """
        Очищает таблицу проектов (последние 100)
        """
        self.cursor.execute("DELETE FROM projects WHERE id IN (SELECT id FROM projects ORDER BY created_at DESC LIMIT 100)")
        self.conn.commit()
