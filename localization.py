"""
Модуль локализации для бота проверки доменов.
Поддерживает несколько языков и автоматическое определение языка пользователя.
"""

import json
import os
import logging
from typing import Dict, Optional
from pathlib import Path

class LocalizationManager:
    """Менеджер локализации для мультиязычной поддержки"""
    
    def __init__(self, locales_dir: str = "locales", default_lang: str = "ru"):
        """
        Инициализация менеджера локализации
        
        Args:
            locales_dir: Директория с файлами переводов
            default_lang: Язык по умолчанию
        """
        self.locales_dir = Path(locales_dir)
        self.default_lang = default_lang
        self.translations: Dict[str, Dict] = {}
        self.supported_languages = []
        self._load_translations()
    
    def _load_translations(self):
        """Загружает все доступные переводы из JSON файлов"""
        try:
            if not self.locales_dir.exists():
                logging.error(f"Locales directory not found: {self.locales_dir}")
                return
            
            for locale_file in self.locales_dir.glob("*.json"):
                lang_code = locale_file.stem
                try:
                    with open(locale_file, "r", encoding="utf-8") as f:
                        self.translations[lang_code] = json.load(f)
                        self.supported_languages.append(lang_code)
                    logging.info(f"✅ Loaded translations for: {lang_code}")
                except Exception as e:
                    logging.error(f"❌ Failed to load {locale_file}: {e}")
            
            if not self.translations:
                logging.warning("⚠️ No translations loaded!")
            else:
                logging.info(f"📚 Loaded {len(self.translations)} language(s): {', '.join(self.supported_languages)}")
                
        except Exception as e:
            logging.error(f"❌ Failed to load translations: {e}")
    
    def get(self, key: str, lang: str = None, **kwargs) -> str:
        """
        Получает перевод по ключу
        
        Args:
            key: Ключ в формате "category.key" (например, "welcome.title")
            lang: Код языка (ru, en, zh). Если None, используется default_lang
            **kwargs: Параметры для форматирования строки
        
        Returns:
            Переведенная строка или ключ, если перевод не найден
        """
        if lang is None:
            lang = self.default_lang
        
        # Fallback на default_lang если язык не поддерживается
        if lang not in self.translations:
            lang = self.default_lang
        
        # Получаем перевод
        try:
            keys = key.split(".")
            value = self.translations[lang]
            
            for k in keys:
                value = value[k]
            
            # Форматируем строку если есть параметры
            if kwargs:
                return value.format(**kwargs)
            return value
            
        except (KeyError, TypeError) as e:
            logging.warning(f"⚠️ Translation not found: {key} for lang: {lang}")
            # Пробуем fallback на default_lang
            if lang != self.default_lang:
                try:
                    value = self.translations[self.default_lang]
                    for k in keys:
                        value = value[k]
                    if kwargs:
                        return value.format(**kwargs)
                    return value
                except:
                    pass
            return key
    
    def get_language_name(self, lang_code: str, in_lang: str = None) -> str:
        """
        Получает название языка
        
        Args:
            lang_code: Код языка для получения названия
            in_lang: На каком языке показать название
        
        Returns:
            Название языка
        """
        if in_lang is None:
            in_lang = self.default_lang
        return self.get(f"languages.{lang_code}", in_lang)
    
    def is_supported(self, lang: str) -> bool:
        """Проверяет, поддерживается ли язык"""
        return lang in self.supported_languages
    
    def normalize_language_code(self, lang_code: Optional[str]) -> str:
        """
        Нормализует код языка из Telegram
        
        Args:
            lang_code: Код языка от Telegram (например, 'ru-RU', 'en-US')
        
        Returns:
            Нормализованный код языка (ru, en, zh)
        """
        if not lang_code:
            return self.default_lang
        
        # Берем только первую часть (ru из ru-RU)
        lang = lang_code.split("-")[0].lower()
        
        # Проверяем поддержку
        if self.is_supported(lang):
            return lang
        
        # Маппинг для особых случаев
        language_mapping = {
            "uk": "ru",  # Украинский -> Русский
            "be": "ru",  # Белорусский -> Русский
            "kk": "ru",  # Казахский -> Русский
            "cn": "zh",  # Китайский
            "zh-cn": "zh",
            "zh-tw": "zh",
        }
        
        return language_mapping.get(lang, self.default_lang)


# Глобальный экземпляр менеджера локализации
i18n = LocalizationManager()


def _(key: str, lang: str = None, **kwargs) -> str:
    """
    Сокращенная функция для получения перевода
    
    Args:
        key: Ключ перевода
        lang: Код языка
        **kwargs: Параметры для форматирования
    
    Returns:
        Переведенная строка
    """
    return i18n.get(key, lang, **kwargs)
