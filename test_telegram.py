#!/usr/bin/env python3
"""
Скрипт для проверки настроек Telegram бота
Проверяет корректность токена и Chat ID
"""

import sys


def check_config():
    """Проверка конфигурации"""
    print("="*60)
    print("🔍 ПРОВЕРКА НАСТРОЕК TELEGRAM")
    print("="*60)
    
    # Проверка импорта config
    try:
        import config
        print("✓ Файл config.py найден")
    except ImportError:
        print("❌ Файл config.py не найден!")
        print("   Создайте файл config.py по инструкции")
        return False
    
    # Проверка токена
    if not hasattr(config, 'TELEGRAM_BOT_TOKEN'):
        print("❌ TELEGRAM_BOT_TOKEN не найден в config.py")
        return False
    
    if config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ TELEGRAM_BOT_TOKEN не настроен!")
        print("   Замените YOUR_BOT_TOKEN_HERE на реальный токен от @BotFather")
        return False
    
    print(f"✓ TELEGRAM_BOT_TOKEN: {config.TELEGRAM_BOT_TOKEN[:20]}...{config.TELEGRAM_BOT_TOKEN[-10:]}")
    
    # Проверка Chat ID
    if not hasattr(config, 'TELEGRAM_CHAT_ID'):
        print("❌ TELEGRAM_CHAT_ID не найден в config.py")
        return False
    
    if config.TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE":
        print("❌ TELEGRAM_CHAT_ID не настроен!")
        print("   Замените YOUR_CHAT_ID_HERE на ваш Chat ID")
        return False
    
    print(f"✓ TELEGRAM_CHAT_ID: {config.TELEGRAM_CHAT_ID}")
    
    return True


def test_bot_connection():
    """Тестирование подключения к боту"""
    print("\n" + "="*60)
    print("🤖 ТЕСТИРОВАНИЕ ПОДКЛЮЧЕНИЯ К БОТУ")
    print("="*60)
    
    try:
        from telegram_bot import TelegramBot
        import config
        
        bot = TelegramBot(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        
        if not bot.test_connection():
            print("\n❌ Не удалось подключиться к боту")
            print("   Проверьте правильность токена")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при подключении: {e}")
        return False


def send_test_message():
    """Отправка тестового сообщения"""
    print("\n" + "="*60)
    print("📨 ОТПРАВКА ТЕСТОВОГО СООБЩЕНИЯ")
    print("="*60)
    
    try:
        from telegram_bot import TelegramBot
        import config
        
        bot = TelegramBot(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        
        test_message = """
🧪 <b>Тестовое сообщение</b>

✅ Telegram бот настроен правильно!
✅ Парсер готов к работе!

📝 Теперь вы можете запустить:
<code>python3 kwork_parser_telegram.py</code>

Удачи! 🚀
"""
        
        if bot.send_message(test_message):
            print("✓ Тестовое сообщение отправлено!")
            print("  Проверьте Telegram")
            return True
        else:
            print("❌ Не удалось отправить сообщение")
            print("   Возможные причины:")
            print("   1. Неправильный Chat ID")
            print("   2. Вы не написали боту /start (для личных сообщений)")
            print("   3. Бот не добавлен в группу (для групповых сообщений)")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при отправке: {e}")
        return False


def main():
    """Основная функция"""
    print("\n")
    
    # Шаг 1: Проверка конфигурации
    if not check_config():
        print("\n" + "="*60)
        print("❌ НАСТРОЙКА НЕ ЗАВЕРШЕНА")
        print("="*60)
        print("\n📖 Прочитайте инструкцию в TELEGRAM_SETUP.md")
        print("🔧 Настройте config.py")
        print("🔄 Запустите этот скрипт снова")
        sys.exit(1)
    
    # Шаг 2: Тестирование подключения
    if not test_bot_connection():
        print("\n" + "="*60)
        print("❌ ОШИБКА ПОДКЛЮЧЕНИЯ")
        print("="*60)
        print("\n🔍 Проверьте:")
        print("   1. Правильность токена бота")
        print("   2. Наличие интернет-соединения")
        print("   3. Существование бота в @BotFather")
        sys.exit(1)
    
    # Шаг 3: Отправка тестового сообщения
    if not send_test_message():
        print("\n" + "="*60)
        print("⚠️  ОШИБКА ОТПРАВКИ СООБЩЕНИЯ")
        print("="*60)
        print("\n🔍 Что делать:")
        print("   1. Если личный чат: напишите боту /start")
        print("   2. Если группа: добавьте бота и сделайте админом")
        print("   3. Проверьте правильность Chat ID")
        print("\n💡 Как узнать Chat ID:")
        print("   - Личный: @userinfobot")
        print("   - Группа: добавьте @userinfobot в группу")
        sys.exit(1)
    
    # Успех!
    print("\n" + "="*60)
    print("🎉 ВСЕ НАСТРОЕНО ПРАВИЛЬНО!")
    print("="*60)
    print("\n✅ Telegram бот работает")
    print("✅ Сообщения доставляются")
    print("✅ Парсер готов к запуску")
    print("\n🚀 Запустите парсер:")
    print("   python3 kwork_parser_telegram.py")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()