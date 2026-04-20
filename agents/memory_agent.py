from db import history_save, history_get, history_get_recent_smart, history_cleanup_old


def remember(role: str, content: str):
    """Сохранить сообщение в долговременную память (БД)."""
    history_save(role, content)


def recall(smart: bool = True) -> list[dict]:
    """
    Вернуть историю диалога.

    Args:
        smart: если True — умное усечение (~2000 токенов, убирает старое),
               если False — последние 20 сообщений (старый способ)

    Returns:
        Список {"role": "...", "content": "..."}
    """
    if smart:
        return history_get_recent_smart(max_tokens=2000)
    return history_get(limit=20)


def cleanup():
    """Очистить историю старше 30 дней (вызывать в фоне периодически)."""
    history_cleanup_old()
