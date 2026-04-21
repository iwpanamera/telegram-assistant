import sqlite3
from datetime import datetime

DB_PATH = "assistant.db"


def init_db():
    """Инициализация базы данных и создание таблиц."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            text         TEXT NOT NULL,
            done         INTEGER NOT NULL DEFAULT 0,
            created      TEXT NOT NULL,
            due          TEXT,
            priority     TEXT NOT NULL DEFAULT 'other',
            category     TEXT NOT NULL DEFAULT 'other',
            type         TEXT NOT NULL DEFAULT 'task',
            asked_review INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Міграція: додати колонки якщо їх ще немає
    migrations = [
        "ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'other'",
        "ALTER TABLE tasks ADD COLUMN category TEXT NOT NULL DEFAULT 'other'",
        "ALTER TABLE tasks ADD COLUMN type TEXT NOT NULL DEFAULT 'task'",
        "ALTER TABLE tasks ADD COLUMN asked_review INTEGER NOT NULL DEFAULT 0",
    ]
    for col_def in migrations:
        try:
            cur.execute(col_def)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # колонка вже є

    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            role    TEXT NOT NULL,
            content TEXT NOT NULL,
            ts      TEXT NOT NULL
        )
    """)

    # Очистка дублікатів подій (залишаємо тільки перший запис по text+due)
    cur.execute("""
        DELETE FROM tasks
        WHERE type = 'event' AND done = 0 AND id NOT IN (
            SELECT MIN(id) FROM tasks
            WHERE type = 'event' AND done = 0
            GROUP BY text, due
        )
    """)

    conn.commit()
    conn.close()


def task_add(
    text: str,
    due: str | None = None,
    priority: str = "other",
    category: str = "other",
    type: str = "task",
) -> int:
    """Додати задачу або подію. Повертає ID нового запису."""
    valid_priorities = {"goal", "habit", "routine", "other"}
    valid_categories = {"work", "family", "church", "health", "finance", "learning", "home", "other"}
    valid_types = {"task", "event"}
    if priority not in valid_priorities:
        priority = "other"
    if category not in valid_categories:
        category = "other"
    if type not in valid_types:
        type = "task"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Для подій: не допускаємо дублікатів (той самий текст + дата)
    if type == "event" and due:
        cur.execute(
            "SELECT id FROM tasks WHERE text = ? AND due = ? AND type = 'event' AND done = 0",
            (text, due),
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            return existing[0]

    now = datetime.now().isoformat(timespec="seconds")
    cur.execute(
        "INSERT INTO tasks (text, done, created, due, priority, category, type, asked_review) VALUES (?, 0, ?, ?, ?, ?, ?, 0)",
        (text, now, due, priority, category, type),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def task_done(task_id: int) -> bool:
    """Отметить задачу/подію виконаною. Повертає True якщо знайдено."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET done = 1 WHERE id = ? AND done = 0", (task_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def tasks_open() -> list[dict]:
    """Вернуть список открытых задач и событий."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, text, created, due, priority, category, type FROM tasks WHERE done = 0 ORDER BY id"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def events_past_unreviewed() -> list[dict]:
    """
    Повернути події, що вже минули і по яких ще не питали 'як пройшло?'.
    Тобто: type='event', done=0, due < now, asked_review=0.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    cur.execute(
        """
        SELECT id, text, due FROM tasks
        WHERE type = 'event'
          AND done = 0
          AND due IS NOT NULL
          AND due < ?
          AND asked_review = 0
        ORDER BY due ASC
        """,
        (now,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def event_mark_reviewed(event_id: int):
    """Позначити що по події вже питали 'як пройшло?'."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET asked_review = 1 WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()


def history_save(role: str, content: str):
    """Сохранить сообщение в историю диалога."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    cur.execute(
        "INSERT INTO history (role, content, ts) VALUES (?, ?, ?)",
        (role, content, now),
    )
    conn.commit()
    conn.close()


def history_get(limit: int = 20) -> list[dict]:
    """Получить последние N сообщений истории."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT role, content, ts FROM (
            SELECT id, role, content, ts FROM history ORDER BY id DESC LIMIT ?
        ) ORDER BY id ASC
        """,
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def history_get_recent_smart(max_tokens: int = 2000) -> list[dict]:
    """
    Получить недавнюю историю (макс ~2000 токенов).
    Убирает сообщения старше 7 дней.
    """
    from datetime import datetime, timedelta
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")

    cur.execute(
        """
        SELECT role, content, ts FROM (
            SELECT id, role, content, ts FROM history
            WHERE ts >= ?
            ORDER BY id DESC LIMIT 50
        ) ORDER BY id ASC
        """,
        (cutoff,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # оценка токенов (примерно 3 символа = 1 токен)
    total_tokens = sum(len(r["content"]) // 3 for r in rows)

    if total_tokens <= max_tokens:
        return rows

    # если не влезает — возвращаем последние 5 + старые если влезают
    if len(rows) > 5:
        result = []
        tokens_left = max_tokens
        last_five = rows[-5:]

        for msg in last_five:
            result.insert(0, msg)
            tokens_left -= len(msg["content"]) // 3

        for msg in rows[:-5]:
            msg_tokens = len(msg["content"]) // 3
            if tokens_left - msg_tokens > 0:
                result.insert(0, msg)
                tokens_left -= msg_tokens

        return result

    return rows


def history_cleanup_old():
    """
    Очистить историю старше 30 дней (архивирование).
    Вызывать периодически для экономии памяти БД.
    """
    from datetime import datetime, timedelta
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    cur.execute("DELETE FROM history WHERE ts < ?", (cutoff,))
    conn.commit()
    conn.close()
