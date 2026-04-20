"""
Утилиты для оптимизации использования токенов и производительности.
"""
import re
from datetime import datetime, timedelta


def is_simple_query(text: str) -> bool:
    """
    Определить, простой ли это запрос (не требует полного контекста).

    Простые запросы: время, список задач, короткие вопросы без контекста.
    """
    simple_patterns = [
        r"^(сколько|что|какой|когда|где|почему|как)\s*",  # вопросы-фразы
        r"^(время|дата|задачи|список|привет|hi|hey)",      # конкретные запросы
        r"^/",                                               # команды
    ]
    text_lower = text.lower().strip()
    return any(re.match(pattern, text_lower) for pattern in simple_patterns) and len(text) < 50


def count_tokens_estimate(text: str) -> int:
    """
    Грубая оценка количества токенов (примерно 4 символа = 1 токен для русского).
    """
    # для русского языка средний токен ~ 2 символа, для английского ~ 4 символа
    # усредняем до 3
    return len(text) // 3


def truncate_history_smart(messages: list[dict], max_tokens: int = 2000) -> list[dict]:
    """
    Умное усечение истории диалога.

    Если история > max_tokens:
    - Оставляем последние N сообщений (они всегда важны)
    - Старые сообщения суммаризируем (в будущем)
    - Убираем сообщения старше 7 дней

    Args:
        messages: список {"role": "...", "content": "...", "ts": "..."}
        max_tokens: максимум токенов для истории

    Returns:
        Усеченный список сообщений
    """
    if not messages:
        return []

    # убираем старые сообщения (> 7 дней)
    cutoff_date = datetime.now() - timedelta(days=7)
    recent = []
    for msg in messages:
        try:
            msg_date = datetime.fromisoformat(msg.get("ts", ""))
            if msg_date >= cutoff_date:
                recent.append(msg)
        except (ValueError, TypeError):
            recent.append(msg)  # если дата невалидна — берём

    # если всё влезает — возвращаем
    total_tokens = sum(count_tokens_estimate(m.get("content", "")) for m in recent)
    if total_tokens <= max_tokens:
        return recent

    # если не влезает — оставляем последние 5 сообщений и убираем старые
    if len(recent) > 5:
        # берём последние 5 сообщений + старые если влезают
        result = []
        tokens_left = max_tokens

        # сначала последние 5
        last_five = recent[-5:]
        for msg in last_five:
            result.insert(0, msg)
            tokens_left -= count_tokens_estimate(msg.get("content", ""))

        # потом добавляем старые если влезают
        for msg in recent[:-5]:
            msg_tokens = count_tokens_estimate(msg.get("content", ""))
            if tokens_left - msg_tokens > 0:
                result.insert(0, msg)
                tokens_left -= msg_tokens

        return result

    return recent


def should_transcribe_voice(duration_seconds: float) -> bool:
    """
    Решить, стоит ли транскрибировать голос.

    Не транскрибируем очень короткие сообщения (< 2 сек).
    """
    return duration_seconds >= 2.0


def extract_voice_duration_from_telegram(voice_obj) -> float:
    """
    Извлечь длительность голосового сообщения (в секундах).
    """
    return getattr(voice_obj, 'duration', 0)


def should_summarize_transcript(text: str) -> bool:
    """
    Решить, нужна ли суммаризация транскрипта перед отправкой Claude.

    Суммаризируем если текст очень длинный (> 500 слов).
    """
    word_count = len(text.split())
    return word_count > 500


def get_system_prompt_size_estimate(system_prompt: str) -> int:
    """
    Оценить размер system prompt в токенах.
    """
    return count_tokens_estimate(system_prompt)


# Кэш для горячих данных (не использует pickle, работает в памяти)
_cache = {
    "tasks": None,
    "tasks_ts": None,
    "memory_hash": None,
    "context_hash": None,
}


def cache_get(key: str):
    """Получить значение из кэша."""
    return _cache.get(key)


def cache_set(key: str, value):
    """Установить значение в кэш."""
    _cache[key] = value


def cache_clear():
    """Очистить кэш."""
    _cache.clear()
