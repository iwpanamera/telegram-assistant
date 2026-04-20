import sqlite3
from datetime import datetime

DB_PATH = "assistant.db"


def init_db():
    """Инициализация базы данных и создание таблиц."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            text     TEXT NOT NULL,
            done     INTEGER NOT NULL DEFAULT 0,
            created  TEXT NOT NULL,
            due      TEXT,
            priority TEXT NOT NULL DEFAULT 'other'
        )
    """)

    # Миграция: добавить колонку priority если её ещё нет
    try:
        cur.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'other'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # колонка уже есть

    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            role    TEXT NOT NULL,
            content TEXT NOT NULL,
            ts      TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def task_add(text: str, due: str | None = None, priority: str = "other") -> int:
    """Добавить задачу. Возвращает ID новой задачи."""
    valid_priorities = {"goal", "routine", "other"}
    if priority not in valid_priorities:
        priority = "other"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    cur.execute(
        "INSERT INTO tasks (text, done, created, due, priority) VALUES (?, 0, ?, ?, ?)",
        (text, now, due, priority),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def task_done(task_id: int) -> bool:
    """Отметить задачу выполненной. Возвращает True если задача найдена."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET done = 1 WHERE id = ? AND done = 0", (task_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def tasks_open() -> list[dict]:
    """Вернуть список открытых задач."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, text, created, due, priority FROM tasks WHERE done = 0 ORDER BY id"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


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
