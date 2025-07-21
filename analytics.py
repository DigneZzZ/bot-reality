import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import redis.asyncio as redis
from collections import defaultdict, Counter
import logging

class AnalyticsCollector:
    """Собирает и анализирует статистику использования бота"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
    
    async def log_domain_check(
        self,
        user_id: int,
        domain: str,
        check_type: str,  # "short" или "full"
        result_status: str,  # "success", "failed", "cached"
        execution_time: Optional[float] = None
    ) -> None:
        """Логирует проверку домена"""
        timestamp = datetime.now().isoformat()
        
        # Общая статистика
        await self.redis.incr("analytics:total_checks")
        await self.redis.incr(f"analytics:daily:{datetime.now().strftime('%Y%m%d')}")
        await self.redis.incr(f"analytics:user:{user_id}:total")
        
        # Статистика по типам проверок
        await self.redis.incr(f"analytics:check_type:{check_type}")
        await self.redis.incr(f"analytics:result_status:{result_status}")
        
        # Детальный лог (храним 30 дней)
        log_entry = {
            "timestamp": timestamp,
            "user_id": user_id,
            "domain": domain,
            "check_type": check_type,
            "result_status": result_status,
            "execution_time": execution_time
        }
        
        await self.redis.lpush(
            "analytics:detailed_logs",
            json.dumps(log_entry)
        )
        await self.redis.expire("analytics:detailed_logs", 86400 * 30)  # 30 дней
        
        # Статистика по доменам
        await self.redis.zincrby("analytics:popular_domains", 1, domain)
        
        # Производительность
        if execution_time:
            await self.redis.lpush(f"analytics:performance:{check_type}", execution_time)
            await self.redis.ltrim(f"analytics:performance:{check_type}", 0, 999)  # Последние 1000
    
    async def log_user_activity(self, user_id: int, action: str, details: Optional[str] = None) -> None:
        """Логирует активность пользователя"""
        timestamp = datetime.now().isoformat()
        
        # Активность пользователя
        await self.redis.incr(f"analytics:user:{user_id}:actions")
        await self.redis.incr(f"analytics:action:{action}")
        
        # Последняя активность
        activity_data = {
            "timestamp": timestamp,
            "action": action,
            "details": details
        }
        await self.redis.set(
            f"analytics:user:{user_id}:last_activity",
            json.dumps(activity_data),
            ex=86400 * 7  # Храним 7 дней
        )
    
    async def get_analytics_summary(self, days: int = 7) -> Dict[str, Any]:
        """Получает сводку аналитики за указанный период"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Базовая статистика
        total_checks = await self.redis.get("analytics:total_checks") or 0
        
        # Статистика по дням
        daily_stats = {}
        for i in range(days):
            date = (end_date - timedelta(days=i)).strftime('%Y%m%d')
            count = await self.redis.get(f"analytics:daily:{date}") or 0
            daily_stats[date] = int(count)
        
        # Популярные домены
        popular_domains = await self.redis.zrevrange("analytics:popular_domains", 0, 9, withscores=True)
        
        # Статистика по типам проверок
        short_checks = await self.redis.get("analytics:check_type:short") or 0
        full_checks = await self.redis.get("analytics:check_type:full") or 0
        
        # Статистика по результатам
        success_count = await self.redis.get("analytics:result_status:success") or 0
        failed_count = await self.redis.get("analytics:result_status:failed") or 0
        cached_count = await self.redis.get("analytics:result_status:cached") or 0
        
        # Производительность
        performance_stats = await self._get_performance_stats()
        
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "total_checks": int(total_checks),
            "daily_stats": daily_stats,
            "popular_domains": [(domain.decode() if isinstance(domain, bytes) else domain, int(score)) 
                              for domain, score in popular_domains],
            "check_types": {
                "short": int(short_checks),
                "full": int(full_checks)
            },
            "results": {
                "success": int(success_count),
                "failed": int(failed_count),
                "cached": int(cached_count)
            },
            "performance": performance_stats
        }
    
    async def _get_performance_stats(self) -> Dict[str, Any]:
        """Получает статистику производительности"""
        stats = {}
        
        for check_type in ["short", "full"]:
            times = await self.redis.lrange(f"analytics:performance:{check_type}", 0, -1)
            if times:
                times = [float(t) for t in times]
                stats[check_type] = {
                    "avg_time": sum(times) / len(times),
                    "min_time": min(times),
                    "max_time": max(times),
                    "total_samples": len(times)
                }
            else:
                stats[check_type] = {
                    "avg_time": 0,
                    "min_time": 0,
                    "max_time": 0,
                    "total_samples": 0
                }
        
        return stats
    
    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Получает статистику конкретного пользователя"""
        total_checks = await self.redis.get(f"analytics:user:{user_id}:total") or 0
        total_actions = await self.redis.get(f"analytics:user:{user_id}:actions") or 0
        
        # Последняя активность
        last_activity_data = await self.redis.get(f"analytics:user:{user_id}:last_activity")
        last_activity = None
        if last_activity_data:
            try:
                last_activity = json.loads(last_activity_data)
            except json.JSONDecodeError:
                pass
        
        return {
            "user_id": user_id,
            "total_checks": int(total_checks),
            "total_actions": int(total_actions),
            "last_activity": last_activity
        }
    
    async def generate_analytics_report(self, admin_id: int) -> str:
        """Генерирует текстовый отчет для администратора"""
        summary = await self.get_analytics_summary(days=7)
        
        report = "📊 <b>Аналитика бота (7 дней)</b>\n\n"
        
        # Общая статистика
        report += f"🔢 <b>Общая статистика:</b>\n"
        report += f"• Всего проверок: {summary['total_checks']}\n"
        report += f"• Успешных: {summary['results']['success']}\n"
        report += f"• Неудачных: {summary['results']['failed']}\n"
        report += f"• Из кэша: {summary['results']['cached']}\n\n"
        
        # Типы проверок
        total_type_checks = summary['check_types']['short'] + summary['check_types']['full']
        if total_type_checks > 0:
            short_pct = (summary['check_types']['short'] / total_type_checks) * 100
            full_pct = (summary['check_types']['full'] / total_type_checks) * 100
            report += f"📋 <b>Типы проверок:</b>\n"
            report += f"• Краткие: {summary['check_types']['short']} ({short_pct:.1f}%)\n"
            report += f"• Полные: {summary['check_types']['full']} ({full_pct:.1f}%)\n\n"
        
        # Популярные домены
        if summary['popular_domains']:
            report += f"🌐 <b>Топ-5 доменов:</b>\n"
            for i, (domain, count) in enumerate(summary['popular_domains'][:5], 1):
                report += f"{i}. {domain} ({count} раз)\n"
            report += "\n"
        
        # Производительность
        perf = summary['performance']
        if perf['short']['total_samples'] > 0:
            report += f"⚡ <b>Производительность:</b>\n"
            report += f"• Краткие: {perf['short']['avg_time']:.1f}с (среднее)\n"
            report += f"• Полные: {perf['full']['avg_time']:.1f}с (среднее)\n\n"
        
        # Активность по дням
        report += f"📅 <b>Активность по дням:</b>\n"
        for date, count in sorted(summary['daily_stats'].items(), reverse=True)[:7]:
            date_formatted = datetime.strptime(date, '%Y%m%d').strftime('%d.%m')
            report += f"• {date_formatted}: {count} проверок\n"
        
        return report

    async def cleanup_old_data(self, days_to_keep: int = 30) -> None:
        """Очищает старые данные аналитики"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        # Очищаем дневную статистику
        for i in range(days_to_keep, days_to_keep + 30):  # Проверяем еще 30 дней назад
            old_date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
            await self.redis.delete(f"analytics:daily:{old_date}")
        
        logging.info(f"Cleaned up analytics data older than {days_to_keep} days")
