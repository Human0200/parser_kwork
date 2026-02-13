#!/usr/bin/env python3
"""
Запуск парсера в фоновом режиме (daemon)
Использует nohup для работы в фоне даже после выхода из терминала
"""

import subprocess
import sys
import os
import time


def start_daemon():
    """Запуск парсера в фоновом режиме"""
    print("="*60)
    print("🚀 ЗАПУСК ПАРСЕРА В ФОНОВОМ РЕЖИМЕ")
    print("="*60)
    
    # Проверка существования основного скрипта
    if not os.path.exists('kwork_parser_telegram.py'):
        print("❌ Файл kwork_parser_telegram.py не найден!")
        print("   Запустите скрипт из директории с парсером")
        sys.exit(1)
    
    # Проверка config.py
    try:
        import config
        if config.RUN_MODE != 'loop':
            print("⚠️  ВНИМАНИЕ: RUN_MODE в config.py установлен в 'once'")
            print("   Парсер завершится после одного запуска")
            print("   Измените RUN_MODE = 'loop' для непрерывной работы")
            print()
            response = input("Продолжить? (y/n): ")
            if response.lower() != 'y':
                print("Отменено")
                sys.exit(0)
    except ImportError:
        print("⚠️  config.py не найден, используются настройки по умолчанию")
    
    # Имя лог-файла
    log_file = "kwork_parser.log"
    pid_file = "kwork_parser.pid"
    
    print(f"\n📝 Логи будут сохраняться в: {log_file}")
    print(f"📌 PID будет сохранен в: {pid_file}")
    
    # Запуск в фоне
    try:
        # Команда для запуска
        cmd = f"nohup python3 kwork_parser_telegram.py >> {log_file} 2>&1 &"
        
        # Запускаем процесс
        process = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setpgrp  # Создаем новую группу процессов
        )
        
        # Небольшая пауза
        time.sleep(2)
        
        # Получаем PID
        pid = process.pid
        
        # Сохраняем PID в файл
        with open(pid_file, 'w') as f:
            f.write(str(pid))
        
        print(f"\n✅ Парсер запущен в фоновом режиме!")
        print(f"📌 PID процесса: {pid}")
        print(f"\n📊 Полезные команды:")
        print(f"   Просмотр логов:     tail -f {log_file}")
        print(f"   Остановка парсера:  python3 stop_daemon.py")
        print(f"   Или вручную:        kill {pid}")
        print(f"   Проверка статуса:   ps aux | grep kwork_parser_telegram.py")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ Ошибка запуска: {e}")
        sys.exit(1)


def stop_daemon():
    """Остановка парсера в фоновом режиме"""
    print("="*60)
    print("⛔ ОСТАНОВКА ПАРСЕРА")
    print("="*60)
    
    pid_file = "kwork_parser.pid"
    
    if not os.path.exists(pid_file):
        print("❌ PID файл не найден")
        print("   Парсер не запущен или был остановлен вручную")
        
        # Попробуем найти процесс вручную
        try:
            result = subprocess.run(
                ["pgrep", "-f", "kwork_parser_telegram.py"],
                capture_output=True,
                text=True
            )
            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                print(f"\n⚠️  Найдены запущенные процессы: {', '.join(pids)}")
                print("   Остановите их вручную: kill <PID>")
            else:
                print("   Процессы не найдены")
        except:
            pass
        
        sys.exit(1)
    
    # Читаем PID
    with open(pid_file, 'r') as f:
        pid = int(f.read().strip())
    
    print(f"📌 PID процесса: {pid}")
    
    # Останавливаем процесс
    try:
        os.kill(pid, 15)  # SIGTERM
        print("✅ Сигнал остановки отправлен")
        
        # Ждем завершения
        time.sleep(2)
        
        # Проверяем, завершился ли процесс
        try:
            os.kill(pid, 0)  # Проверка существования процесса
            print("⚠️  Процесс еще работает, отправляем SIGKILL...")
            os.kill(pid, 9)  # SIGKILL
        except OSError:
            print("✅ Парсер успешно остановлен")
        
        # Удаляем PID файл
        os.remove(pid_file)
        
    except ProcessLookupError:
        print("⚠️  Процесс уже был остановлен")
        os.remove(pid_file)
    except Exception as e:
        print(f"❌ Ошибка при остановке: {e}")
        sys.exit(1)
    
    print("="*60)


def check_status():
    """Проверка статуса парсера"""
    print("="*60)
    print("📊 СТАТУС ПАРСЕРА")
    print("="*60)
    
    pid_file = "kwork_parser.pid"
    
    if not os.path.exists(pid_file):
        print("⛔ Парсер НЕ запущен")
        print("   PID файл не найден")
        print("\n💡 Запустите: python3 start_daemon.py")
        print("="*60)
        return
    
    # Читаем PID
    with open(pid_file, 'r') as f:
        pid = int(f.read().strip())
    
    # Проверяем, работает ли процесс
    try:
        os.kill(pid, 0)
        print("✅ Парсер РАБОТАЕТ")
        print(f"📌 PID: {pid}")
        
        # Пытаемся получить информацию о процессе
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "etime=,rss="],
                capture_output=True,
                text=True
            )
            if result.stdout.strip():
                lines = result.stdout.strip().split()
                if len(lines) >= 2:
                    print(f"⏱️  Время работы: {lines[0]}")
                    print(f"💾 Память: {int(lines[1]) / 1024:.1f} MB")
        except:
            pass
        
        # Проверяем размер лог-файла
        log_file = "kwork_parser.log"
        if os.path.exists(log_file):
            size = os.path.getsize(log_file)
            print(f"📝 Размер лога: {size / 1024:.1f} KB")
            print(f"\n💡 Просмотр логов: tail -f {log_file}")
        
    except OSError:
        print("⛔ Парсер НЕ работает")
        print(f"   PID {pid} не найден (процесс был остановлен)")
        print(f"   Удалите файл {pid_file} вручную")
    
    print("="*60)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "start":
            start_daemon()
        elif command == "stop":
            stop_daemon()
        elif command == "status":
            check_status()
        else:
            print("Неизвестная команда. Используйте: start, stop, status")
    else:
        # По умолчанию - запуск
        start_daemon()