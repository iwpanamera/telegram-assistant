"""
conftest.py — загальні фікстури pytest для тестів асистента.

Додає корінь проекту в sys.path, щоб тести могли імпортувати db/agents.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Тести не повинні потребувати реального DATABASE_URL / API-ключів.
# Модулі, які підключаються до зовнішніх сервісів, імпортуємо ліниво
# всередині окремих тест-функцій.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test_fake")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("MY_CHAT_ID", "0")
