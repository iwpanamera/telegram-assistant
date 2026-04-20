import json
import re
import hashlib
from datetime import datetime, timedelta
from db import task_add, task_done, tasks_open
from agents.memory_loop import update_memory
from agents.optimization_utils import cache_get, cache_set

_MONTHS_UA = [
    "", "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"
]


def _fmt_due(due: str) -> str:
    """Перетворює '2026-04-19T20:00' на '19 квітня о 20:00'."""
    if not due:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(due, fmt)
            month = _MONTHS_UA[dt.month]
            if fmt == "%Y-%m-%d":
                return f"{dt.day} {month}"
            return f"{dt.day} {month} о {dt.strftime('%H:%M')}"
        except ValueError:
            continue
    return due


# ---------------------------------------------------------------------------
# Отримання задач
# ---------------------------------------------------------------------------

def get_tasks(use_cache: bool = True) -> list[dict]:
    """
    Повернути список відкритих задач і подій з БД.

    Args:
        use_cache: якщо True — перевіряє кеш, валідний 30 сек

    Returns:
        Список відкритих записів
    """
    if use_cache:
        cached = cache_get("tasks")
        ts = cache_get("tasks_ts")

        if cached is not None and ts is not None:
            if datetime.now() - ts < timedelta(seconds=30):
                return cached

    tasks = tasks_open()

    if use_cache:
        cache_set("tasks", tasks)
        cache_set("tasks_ts", datetime.now())

    return tasks


# ---------------------------------------------------------------------------
# Форматування для системного промпту
# ---------------------------------------------------------------------------

_PRIORITY_LABEL = {
    "goal":    "[Ціль А]",
    "habit":   "[Звичка В]",
    "routine": "[Рутина Б]",
    "other":   "[Інше Г]",
}

_CATEGORY_LABEL = {
    "work":     "[робота]",
    "family":   "[сім'я]",
    "church":   "[церква]",
    "health":   "[здоров'я]",
    "finance":  "[фінанси]",
    "learning": "[навчання]",
    "home":     "[дім]",
    "other":    "[інше]",
}


def format_tasks_for_prompt() -> str:
    """
    Повернути короткий список відкритих задач і подій для системного промпту.
    """
    records = get_tasks()
    if not records:
        return "Відкритих задач і подій немає."

    tasks = [r for r in records if r.get("type", "task") == "task"]
    events = [r for r in records if r.get("type", "task") == "event"]

    lines = []

    if tasks:
        lines.append("Задачі:")
        for t in tasks:
            due_part = f" (до {_fmt_due(t['due'])})" if t.get("due") else ""
            priority = t.get("priority", "other")
            category = t.get("category", "other")
            label = _PRIORITY_LABEL.get(priority, "[Інше]")
            cat_label = _CATEGORY_LABEL.get(category, "[інше]")
            lines.append(f"  [{t['id']}] {label} {cat_label} {t['text']}{due_part}")

    if events:
        lines.append("Події:")
        for e in events:
            due_part = f" ({_fmt_due(e['due'])})" if e.get("due") else ""
            lines.append(f"  [{e['id']}] [подія] {e['text']}{due_part}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Форматування для користувача
# ---------------------------------------------------------------------------

def format_tasks_for_user() -> str:
    """
    Повернути список задач і подій для відображення користувачу.
    Без емоджі. Групування задач: А -> В -> Б -> Г. Події — окремо.
    """
    records = get_tasks()
    if not records:
        return "Відкритих задач і подій немає."

    tasks = [r for r in records if r.get("type", "task") == "task"]
    events = [r for r in records if r.get("type", "task") == "event"]

    lines = []

    # --- Задачі ---
    if tasks:
        groups = {"goal": [], "habit": [], "routine": [], "other": []}
        for t in tasks:
            p = t.get("priority", "other")
            groups.get(p, groups["other"]).append(t)

        lines.append("Задачі:")
        order = [
            ("goal",    "Ціль (А)"),
            ("habit",   "Звичка (В)"),
            ("routine", "Рутина (Б)"),
            ("other",   "Інше (Г)"),
        ]
        for priority_key, label in order:
            group = groups[priority_key]
            if not group:
                continue
            lines.append(f"\n{label}")
            for t in group:
                due_part = f" (до {_fmt_due(t['due'])})" if t.get("due") else ""
                cat_label = _CATEGORY_LABEL.get(t.get("category", "other"), "[інше]")
                lines.append(f"  [{t['id']}] {cat_label} {t['text']}{due_part}")

    # --- Події ---
    if events:
        if lines:
            lines.append("")
        lines.append("Події:")
        for e in events:
            due_part = f" — {_fmt_due(e['due'])}" if e.get("due") else ""
            lines.append(f"  [{e['id']}] {e['text']}{due_part}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Виконання команд від Claude
# ---------------------------------------------------------------------------

def execute_commands(commands: list[dict]) -> str:
    """
    Виконати список JSON-команд, повернутих Claude.

    Підтримувані команди:
        {"action": "add_task", "text": "...", "due": "...", "priority": "...", "category": "..."}
        {"action": "add_event", "text": "...", "due": "..."}
        {"action": "done_task", "id": N}
        {"action": "update_memory", "section": "...", "content": "..."}

    Returns:
        Рядок з результатами виконання команд.
    """
    results = []
    tasks_mutated = False
    for cmd in commands:
        action = cmd.get("action")

        if action == "add_task":
            text = cmd.get("text", "").strip()
            due = cmd.get("due") or None
            priority = cmd.get("priority", "other")
            category = cmd.get("category", "other")
            if text:
                new_id = task_add(text, due, priority, category, type="task")
                due_note = f" (до {_fmt_due(due)})" if due else ""
                p_label = _PRIORITY_LABEL.get(priority, "[Інше]")
                results.append(f"Задачу додано [{new_id}] {p_label}: {text}{due_note}")
                tasks_mutated = True

        elif action == "add_event":
            text = cmd.get("text", "").strip()
            due = cmd.get("due") or None
            if text:
                new_id = task_add(text, due, priority="other", category="other", type="event")
                due_note = f" — {_fmt_due(due)}" if due else ""
                results.append(f"Подію додано [{new_id}]: {text}{due_note}")
                tasks_mutated = True

        elif action == "done_task":
            task_id = cmd.get("id")
            if task_id is not None:
                ok = task_done(int(task_id))
                if ok:
                    results.append(f"Задача [{task_id}] — [виконано].")
                    tasks_mutated = True
                else:
                    results.append(f"Задача [{task_id}] не знайдена або вже закрита.")

        elif action == "update_memory":
            section = cmd.get("section", "").strip()
            content = cmd.get("content", "").strip()
            if section and content:
                update_memory(section, content)

        else:
            pass  # невідомі команди ігноруємо

    # Інвалідуємо кеш після будь-якої мутації
    if tasks_mutated:
        cache_set("tasks_ts", None)

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Швидке закриття задачі (для команди /done)
# ---------------------------------------------------------------------------

def close(task_id: int) -> bool:
    """Закрити задачу по ID. Повертає True при успіху."""
    return task_done(task_id)


# ---------------------------------------------------------------------------
# Парсинг JSON-команд з відповіді Claude
# ---------------------------------------------------------------------------

def parse_commands_from_response(answer: str) -> tuple[str, list[dict]]:
    """
    Витягти JSON-команди з відповіді Claude.
    Шукаємо всі JSON-масиви [...] з полем "action".
    Команди прибираються з тексту перед відправкою користувачу.
    """
    all_commands = []
    spans_to_remove = []

    i = 0
    while i < len(answer):
        if answer[i] != '[':
            i += 1
            continue

        depth = 0
        in_string = False
        escape_next = False
        j = i
        while j < len(answer):
            ch = answer[j]
            if escape_next:
                escape_next = False
            elif ch == '\\' and in_string:
                escape_next = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == '[' or ch == '{':
                    depth += 1
                elif ch == ']' or ch == '}':
                    depth -= 1
                    if depth == 0:
                        break
            j += 1

        if depth != 0:
            i += 1
            continue

        raw_json = answer[i:j+1]
        try:
            cmds = json.loads(raw_json)
            if isinstance(cmds, list):
                valid = [c for c in cmds if isinstance(c, dict) and "action" in c]
                if valid:
                    all_commands.extend(valid)
                    start = max(0, i - 1) if i > 0 and answer[i-1] == '\n' else i
                    end = j + 2 if j + 1 < len(answer) and answer[j+1] == '\n' else j + 1
                    spans_to_remove.append((start, end))
        except (json.JSONDecodeError, ValueError):
            pass

        i = j + 1

    clean_text = answer
    for start, end in sorted(spans_to_remove, reverse=True):
        clean_text = clean_text[:start] + clean_text[end:]
    clean_text = clean_text.strip()

    return clean_text, all_commands
