import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from aiogram import Bot
from aiogram.types import Message
import logging

class ProgressTracker:
    """Отслеживает прогресс для множественных проверок доменов"""
    
    def __init__(self, bot: Bot, message: Message, total_domains: int):
        self.bot = bot
        self.message = message
        self.total_domains = total_domains
        self.completed = 0
        self.failed = 0
        self.start_time = datetime.now()
        self.progress_message: Optional[Message] = None
        self.domain_results: Dict[str, str] = {}
        
    async def start(self, domains: List[str]) -> None:
        """Инициализирует progress bar"""
        progress_text = self._generate_progress_text(domains)
        try:
            self.progress_message = await self.message.answer(progress_text)
        except Exception as e:
            logging.error(f"Failed to send initial progress message: {e}")
    
    async def update_domain_status(self, domain: str, status: str, result: Optional[str] = None) -> None:
        """Обновляет статус конкретного домена"""
        if status == "completed":
            self.completed += 1
            if result:
                self.domain_results[domain] = result
        elif status == "failed":
            self.failed += 1
            
        await self._update_progress_message()
    
    async def finish(self) -> None:
        """Завершает отслеживание прогресса"""
        elapsed_time = (datetime.now() - self.start_time).total_seconds()
        
        final_text = (
            f"✅ <b>Проверка завершена!</b>\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"• Всего доменов: {self.total_domains}\n"
            f"• Успешно: {self.completed}\n"
            f"• Ошибок: {self.failed}\n"
            f"• Время выполнения: {elapsed_time:.1f}с\n"
        )
        
        if self.progress_message:
            try:
                await self.progress_message.edit_text(final_text)
            except Exception as e:
                logging.error(f"Failed to update final progress message: {e}")
    
    def _generate_progress_text(self, domains: Optional[List[str]] = None) -> str:
        """Генерирует текст progress bar"""
        progress_percentage = (self.completed / self.total_domains) * 100 if self.total_domains > 0 else 0
        
        # Визуальный progress bar
        bar_length = 20
        filled_length = int(bar_length * progress_percentage / 100)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        
        elapsed_time = (datetime.now() - self.start_time).total_seconds()
        
        # Оценка оставшегося времени
        if self.completed > 0:
            avg_time_per_domain = elapsed_time / self.completed
            remaining_domains = self.total_domains - self.completed - self.failed
            eta = avg_time_per_domain * remaining_domains
            eta_text = f" | ETA: {eta:.0f}с"
        else:
            eta_text = ""
        
        text = (
            f"🔄 <b>Проверка доменов...</b>\n\n"
            f"[{bar}] {progress_percentage:.1f}%\n\n"
            f"📊 <b>Прогресс:</b> {self.completed + self.failed}/{self.total_domains}\n"
            f"✅ Завершено: {self.completed}\n"
            f"❌ Ошибок: {self.failed}\n"
            f"⏱ Время: {elapsed_time:.1f}с{eta_text}"
        )
        
        return text
    
    async def _update_progress_message(self) -> None:
        """Обновляет сообщение с прогрессом"""
        if not self.progress_message:
            return
            
        try:
            new_text = self._generate_progress_text()
            await self.progress_message.edit_text(new_text)
        except Exception as e:
            # Telegram может ограничивать частоту обновлений
            if "message is not modified" not in str(e).lower():
                logging.warning(f"Failed to update progress message: {e}")

class BatchProcessor:
    """Обрабатывает домены батчами с прогрессом"""
    
    def __init__(self, bot: Bot, batch_size: int = 3, delay_between_batches: float = 1.0):
        self.bot = bot
        self.batch_size = batch_size
        self.delay_between_batches = delay_between_batches
    
    async def process_domains(
        self,
        domains: List[str],
        user_id: int,
        message: Message,
        check_function,
        short_mode: bool = True
    ) -> Dict[str, Any]:
        """
        Обрабатывает домены батчами с отображением прогресса
        
        Args:
            domains: Список доменов для проверки
            user_id: ID пользователя
            message: Сообщение для отображения прогресса
            check_function: Функция проверки домена
            short_mode: Краткий режим отчета
            
        Returns:
            Словарь с результатами
        """
        total_domains = len(domains)
        tracker = ProgressTracker(self.bot, message, total_domains)
        
        await tracker.start(domains)
        
        results = {
            "successful": [],
            "failed": [],
            "cached": [],
            "errors": []
        }
        
        # Обрабатываем домены батчами
        for i in range(0, len(domains), self.batch_size):
            batch = domains[i:i + self.batch_size]
            
            # Создаем задачи для текущего батча
            tasks = []
            for domain in batch:
                task = self._process_single_domain(
                    domain, user_id, check_function, short_mode, tracker, results
                )
                tasks.append(task)
            
            # Выполняем батч параллельно
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # Пауза между батчами (кроме последнего)
            if i + self.batch_size < len(domains):
                await asyncio.sleep(self.delay_between_batches)
        
        await tracker.finish()
        return results
    
    async def _process_single_domain(
        self,
        domain: str,
        user_id: int,
        check_function,
        short_mode: bool,
        tracker: ProgressTracker,
        results: Dict[str, Any]
    ) -> None:
        """Обрабатывает один домен"""
        try:
            result = await check_function(domain, user_id, short_mode)
            
            if result and "кэша" in result:
                results["cached"].append(domain)
            else:
                results["successful"].append(domain)
                
            await tracker.update_domain_status(domain, "completed", result)
            
        except Exception as e:
            logging.error(f"Error processing domain {domain}: {e}")
            results["failed"].append(domain)
            results["errors"].append(f"{domain}: {str(e)}")
            await tracker.update_domain_status(domain, "failed")
