import os
import psycopg2
from datetime import datetime
from contextlib import contextmanager
import pytz

# Встановлюємо часовий пояс для всіх datetime операцій
_TZ = pytz.timezone('Europe/Kyiv')

# Читаємо DATABASE_URL з環境
DATABASE_URL = os.getenv("DATABASE_URL", "")


@contextmanager
def get_db():
    """Контекст-менеджер для PostgreSQL подключення."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Інініціалізація БД та створення таблиць."""
    with get_db() as conn:
        cur = conn.cursor()

        # Створюємо таблицю tasks з усіма колонками
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id           SERIAL PRIMARY KEY,
                text         TEXT NOT NULL,
                done         INTEGER NOT NULL DEFAULT 0,
                created      TEXT NOT NULL,
                due          TEXT,
                priority     TEXT NOT NULL DEFAULT 'other',
                category     TEXT NOT NULL DEFAULT 'other',
                type         TEXT NOT NULL DEFAULT 'task',
                asked_review INTEGER NOT NULL DEFAULT 0,
                streak       INTEGER NOT NULL DEFAULT 0,
                last_done    TEXT
            )
        """)

        # Міграції: додаємо колонки якщо їх нема (IF NOT EXISTS для PostgreSQL)
        migrations = [
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'other'",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'other'",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'task'",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS asked_review INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS streak INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS last_done TEXT",
        ]
        for col_def in migrations:
            try:
                cur.execute(col_def)
                conn.commit()
            except Exception as e:
                conn.rollback()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id      SERIAL PRIMARY KEY,
                role    TEXT NOT NULL,
                content TEXT NOT NULL,
                ts      TEXT NOT NULL
            )
        """)

        # Таблиця напоминаний
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id       SERIAL PRIMARY KEY,
                text     TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                created  TEXT NOT NULL,
                done     INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Очистка дублікатів подій
        try:
            cur.execute("""
                DELETE FROM tasks
                WHERE type = 'event' AND done = 0 AND id NOT IN (
                    SELECT MIN(id) FROM tasks
                    WHERE type = 'event' AND done = 0
                    GROUP BY text, due
                )
            """)
        except Exception:
            pass  # таблиця порожня

        conn.commit()


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

    with get_db() as conn:
        cur = conn.cursor()

        # Для подій: не допускаємо дублікатів (той самий текст + дата)
        if type == "event" and due:
            cur.execute(
                "SELECT id FROM tasks WHERE text = %s AND due = %s AND type = 'event' AND done = 0",
                (text, due),
            )
            existing = cur.fetchone()
            if existing:
                return existing[0]

        now = datetime.now(_TZ).isoformat(timespec="seconds")
        cur.execute(
            "INSERT INTO tasks (text, done, created, due, priority, category, type, asked_review) VALUES (%s, 0, %s, %s, %s, %s, %s, 0) RETURNING id",
            (text, now, due, priority, category, type),
        )
        task_id = cur.fetchone()[0]
        conn.commit()
        return task_id


def task_done(task_id: int) -> bool:
    """
    Отметить задачу/подію виконаною. Повертає True якщо знайдено.
    Встановлює last_done, щоб тижневий огляд міг вибирати по часу закриття.
    """
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        cur.execute(
            "UPDATE tasks SET done = 1, last_done = %s WHERE id = %s AND done = 0",
            (now, task_id),
        )
        changed = cur.rowcount > 0
        conn.commit()
        return changed


def tasks_open() -> list[dict]:
    """Вернуть список открытых задач и событий."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, text, created, due, priority, category, type FROM tasks WHERE done = 0 ORDER BY id"
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        return rows


def events_past_unreviewed() -> list[dict]:
    """
    Повернути події, що вже минули і по яких ще не питали 'як пройшло?'.
    Тобто: type='event', done=0, due < now, asked_review=0.
    """
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        cur.execute(
            """
            SELECT id, text, due FROM tasks
            WHERE type = 'event'
              AND done = 0
              AND due IS NOT NULL
              AND due < %s
              AND asked_review = 0
            ORDER BY due ASC
            """,
            (now,),
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        return rows


def event_mark_reviewed(event_id: int):
    """Позначити що по події вже питали 'як пройшло?'."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE tasks SET asked_review = 1 WHERE id = %s", (event_id,))
        conn.commit()


def history_save(role: str, content: str):
    """Сохранить сообщение в историю диалога."""
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        cur.execute(
            "INSERT INTO history (role, content, ts) VALUES (%s, %s, %s)",
            (role, content, now),
        )
        conn.commit()


def history_get(limit: int = 20) -> list[dict]:
    """Получить последние N сообщений истории."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT role, content, ts FROM (
                SELECT id, role, content, ts FROM history ORDER BY id DESC LIMIT %s
            ) t ORDER BY id ASC
            """,
            (limit,),
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        return rows


def history_get_recent_smart(max_tokens: int = 2000) -> list[dict]:
    """
    Получить недавнюю історію (макс ~2000 токенів).
    Убирає повідомлення старше 7 днів.
    """
    from datetime import timedelta

    with get_db() as conn:
        cur = conn.cursor()

        cutoff = (datetime.now(_TZ) - timedelta(days=7)).isoformat(timespec="seconds")

        cur.execute(
            """
            SELECT role, content, ts FROM (
                SELECT id, role, content, ts FROM history
                WHERE ts >= %s
                ORDER BY id DESC LIMIT 50
            ) t ORDER BY id ASC
            """,
            (cutoff,),
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

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
    Вызывать періодично для экономии памяти БД.
    """
    from datetime import timedelta

    with get_db() as conn:
        cur = conn.cursor()

        cutoff = (datetime.now(_TZ) - timedelta(days=30)).isoformat(timespec="seconds")
        cur.execute("DELETE FROM history WHERE ts < %s", (cutoff,))
        conn.commit()


# ---------------------------------------------------------------------------
# Функції для привичок (habits)
# ---------------------------------------------------------------------------

def habit_increment_streak(task_id: int):
    """Інкрементувати полоску дней подряд для привычки."""
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        cur.execute(
            "UPDATE tasks SET streak = streak + 1, last_done = %s WHERE id = %s AND type = 'task'",
            (now, task_id),
        )
        conn.commit()


def habit_check_streak(task_id: int) -> int:
    """Получить текущую полоску привычки."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT streak FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        return row[0] if row else 0


def habit_daily_reset() -> int:
    """
    Щоденно: відкрити закриті привички заново, щоб було видно на сьогодні.
    Повертає кількість перевідкритих привичок.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tasks
            SET done = 0
            WHERE type = 'task' AND priority = 'habit' AND done = 1
            """
        )
        count = cur.rowcount
        conn.commit()
        return count


def tasks_closed_since(cutoff_iso: str) -> list[dict]:
    """
    Повернути задачі закриті з певного часу (last_done >= cutoff).
    Використовується для тижневого огляду.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, text, priority, category, type, last_done, due
            FROM tasks
            WHERE done = 1
              AND (last_done >= %s OR (last_done IS NULL AND created >= %s))
            ORDER BY COALESCE(last_done, created) DESC
            """,
            (cutoff_iso, cutoff_iso),
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        return rows


def history_get_older_than(days: int = 7, limit: int = 100) -> list[dict]:
    """
    Повернути повідомлення старше N днів для суммаризації.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cutoff = (datetime.now(_TZ) - timedelta(days=days)).isoformat(timespec="seconds")
        cur.execute(
            """
            SELECT id, role, content, ts FROM history
            WHERE ts < %s
            ORDER BY id ASC
            LIMIT %s
            """,
            (cutoff, limit),
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def history_delete_by_ids(ids: list[int]):
    """Видалити повідомлення історії по списку id."""
    if not ids:
        return
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM history WHERE id = ANY(%s)", (ids,))
        conn.commit()


def habit_reset_stale_streaks():
    """
    Сбросить полоски для привичек, которые не выполнялись более дня.
    Вызывать раз в день.
    """
    from datetime import timedelta

    with get_db() as conn:
        cur = conn.cursor()

        # Получаем привычки, которые не выполнялись > 24 часа
        cutoff = (datetime.now(_TZ) - timedelta(hours=24)).isoformat(timespec="seconds")
        cur.execute(
            """
            UPDATE tasks
            SET streak = 0
            WHERE type = 'task'
              AND priority = 'habit'
              AND done = 0
              AND (last_done IS NULL OR last_done < %s)
            """,
            (cutoff,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Функції для напоминаний (reminders)
# ---------------------------------------------------------------------------

def reminder_add(text: str, remind_at: str) -> int:
    """Додати напоминання на певний час. Повертає ID."""
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        cur.execute(
            "INSERT INTO reminders (text, remind_at, created, done) VALUES (%s, %s, %s, 0) RETURNING id",
            (text, remind_at, now),
        )
        reminder_id = cur.fetchone()[0]
        conn.commit()
        return reminder_id


def reminders_pending() -> list[dict]:
    """Получить напоминания, которые должны сработать сейчас или в прошлом."""
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        cur.execute(
            """
            SELECT id, text, remind_at FROM reminders
            WHERE done = 0 AND remind_at <= %s
            ORDER BY remind_at ASC
            """,
            (now,),
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        return rows


def reminder_mark_done(reminder_id: int):
    """Отметить напоминание как показанное."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE reminders SET done = 1 WHERE id = %s", (reminder_id,))
        conn.commit()
