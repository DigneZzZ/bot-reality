# Улучшения для бота проверки доменов

## 1. 📊 Мониторинг и метрики

### Проблема
Нет visibility в работу бота - сколько проверок, какие ошибки, производительность

### Решение
```python
# metrics.py
import time
from collections import defaultdict
from datetime import datetime, timedelta

class BotMetrics:
    def __init__(self):
        self.checks_count = defaultdict(int)
        self.errors_count = defaultdict(int)
        self.response_times = []
        
    async def track_check(self, domain: str, duration: float, success: bool):
        today = datetime.now().strftime("%Y-%m-%d")
        self.checks_count[today] += 1
        self.response_times.append(duration)
        if not success:
            self.errors_count[today] += 1
            
    async def get_stats(self) -> dict:
        return {
            "daily_checks": dict(self.checks_count),
            "daily_errors": dict(self.errors_count),
            "avg_response_time": sum(self.response_times[-100:]) / len(self.response_times[-100:]) if self.response_times else 0
        }
```

### Команды админа
- `/stats` - статистика за день/неделю
- `/health` - статус компонентов бота
- `/performance` - метрики производительности
