# Tests

Запуск:

```bash
cd assistant
pip install pytest
pytest tests/ -v
```

Тести НЕ роблять реальних викликів до Anthropic/Groq/Postgres.
Всі залежні від IO функції або ізольовані (чисті), або мокаються через fake-об'єкти.
