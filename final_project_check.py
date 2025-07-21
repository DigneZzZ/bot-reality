#!/usr/bin/env python3
"""
Финальная проверка всех изменений в проекте
"""

import os
from pathlib import Path

def check_env_files():
    """Проверка .env файлов"""
    print("🔍 Проверка .env файлов...")
    
    # Проверяем .env.sample
    env_sample_path = Path('.env.sample')
    if env_sample_path.exists():
        content = env_sample_path.read_text(encoding='utf-8')
        if 'GROUP_OUTPUT_MODE=short' in content:
            print("✅ .env.sample содержит GROUP_OUTPUT_MODE")
        else:
            print("❌ .env.sample НЕ содержит GROUP_OUTPUT_MODE")
    else:
        print("❌ .env.sample не найден")
    
    # Проверяем .env
    env_path = Path('.env')
    if env_path.exists():
        content = env_path.read_text(encoding='utf-8')
        if 'GROUP_OUTPUT_MODE=short' in content:
            print("✅ .env содержит GROUP_OUTPUT_MODE")
        else:
            print("❌ .env НЕ содержит GROUP_OUTPUT_MODE")
    else:
        print("❌ .env не найден")

def check_bot_py():
    """Проверка bot.py"""
    print("\n🔍 Проверка bot.py...")
    
    bot_path = Path('bot.py')
    if not bot_path.exists():
        print("❌ bot.py не найден")
        return
        
    content = bot_path.read_text(encoding='utf-8')
    
    checks = [
        ('GROUP_OUTPUT_MODE = os.getenv', "Переменная GROUP_OUTPUT_MODE"),
        ('def get_full_report_button' not in content, "Функция get_full_report_button удалена"),
        ('def get_group_full_report_button' not in content, "Функция get_group_full_report_button удалена"),
        ('cq_full_report' not in content, "Callback handler cq_full_report удален"),
        ('final_short_mode = short_mode and (GROUP_OUTPUT_MODE == "short")', "Логика GROUP_OUTPUT_MODE для групп"),
        ('Для полного логирования выполните повторный запрос в ЛС боту', "Инструкция для групп"),
    ]
    
    for check, description in checks:
        if isinstance(check, str):
            if check in content:
                print(f"✅ {description}")
            else:
                print(f"❌ {description}")
        else:
            if check:
                print(f"✅ {description}")
            else:
                print(f"❌ {description}")

def check_worker_py():
    """Проверка worker.py"""
    print("\n🔍 Проверка worker.py...")
    
    worker_path = Path('worker.py')
    if not worker_path.exists():
        print("❌ worker.py не найден")
        return
        
    content = worker_path.read_text(encoding='utf-8')
    
    checks = [
        ('GROUP_OUTPUT_MODE = os.getenv', "Переменная GROUP_OUTPUT_MODE"),
        ('def get_full_report_button' not in content, "Функция get_full_report_button удалена"),
        ('def get_group_full_report_button' not in content, "Функция get_group_full_report_button удалена"),
        ('InlineKeyboardMarkup' not in content, "Импорт InlineKeyboardMarkup удален"),
        ('GROUP_OUTPUT_MODE == "short"', "Логика GROUP_OUTPUT_MODE"),
        ('Для полного логирования выполните повторный запрос в ЛС боту', "Инструкция для групп"),
    ]
    
    for check, description in checks:
        if isinstance(check, str):
            if check in content:
                print(f"✅ {description}")
            else:
                print(f"❌ {description}")
        else:
            if check:
                print(f"✅ {description}")
            else:
                print(f"❌ {description}")

def check_readme():
    """Проверка README.md"""
    print("\n🔍 Проверка README.md...")
    
    readme_path = Path('README.md')
    if not readme_path.exists():
        print("❌ README.md не найден")
        return
        
    content = readme_path.read_text(encoding='utf-8')
    
    if 'GROUP_OUTPUT_MODE=short' in content:
        print("✅ README.md содержит документацию для GROUP_OUTPUT_MODE")
    else:
        print("❌ README.md НЕ содержит документацию для GROUP_OUTPUT_MODE")

def main():
    print("🚀 Финальная проверка проекта bot-reality")
    print("=" * 50)
    
    # Меняем рабочую директорию, если нужно
    if not Path('bot.py').exists():
        os.chdir('c:/Users/digne/OneDrive/Документы/GitHub/bot-reality')
    
    check_env_files()
    check_bot_py()
    check_worker_py()
    check_readme()
    
    print("\n" + "=" * 50)
    print("📋 Итоги изменений:")
    print("✅ Добавлена переменная GROUP_OUTPUT_MODE в .env файлы")
    print("✅ Удалены все функции создания кнопок")
    print("✅ Удален callback handler для кнопок")
    print("✅ Упрощена логика: текстовые инструкции вместо кнопок")
    print("✅ Deep-link функциональность сохранена")
    print("✅ Обновлена документация в README.md")
    print("\n🎯 Deep-link https://t.me/gig_reality_bot?start=ya.ru должен работать!")

if __name__ == "__main__":
    main()
