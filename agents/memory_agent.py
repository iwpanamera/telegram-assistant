from db import history_save, history_get


def remember(role: str, content: str):
    """Сохранить сообщение в долговременную память (БД)."""
    history_save(role, content)


def recall() -> list[dict]:
    """Вернуть последние 20 сообщений диалога."""
    return history_get(limit=20)
