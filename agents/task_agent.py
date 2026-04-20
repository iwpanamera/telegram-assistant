import json
import re
import hashlib
from datetime import datetime, timedelta
from db import task_add, task_done, tasks_open  # task_add тепер приймає category
from agents.memory_loop import update_memory
from agents.optimization_utils import cache_get, cache_set

_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]

def _fmt_due(due: str) -> str:
    """Превращает '2026-04-19T20:00' в '19 апреля в 20:00'."""
    if not due:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(due, fmt)
            month = _MONTHS[dt.month]
            if fmt == "%Y-%m-%d":
                return f"{dt.day} {month}"
            return f"{dt.day} {month} в {dt.strftime('%H:%M')}"
        except ValueError:
            continue
    return due  # если формат неизвестный — вернуть как есть


# ---------------------------------------------------------------------------
# Получение задач
# ---------------------------------------------------------------------------

def get_tasks(use_cache: bool = True) -> list[dict]:
    """
    Вернуть список открытых задач из БД.

    Args:
        use_cache: если True — проверяет кэш, валидный 30 сек

    Returns:
        Список открытых задач
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
# Форматирование для системного промпта
# ---------------------------------------------------------------------------

_PRIORITY_LABEL = {
    "goal":    "[ЦІЛЬ А]",
    "habit":   "[ЗВИЧКА В]",
    "routine": "[РУТИНА Б]",
    "other":   "[ІНШЕ Г]",
}

_PRIORITY_ICON = {
    "goal":    "🔴",
    "habit":   "🟢",
    "routine": "🟡",
    "other":   "⚪",
}

_CATEGORY_ICON = {
    "work":     "💼",
    "family":   "👨‍👩‍👧",
    "church":   "✝️",
    "health":   "💪",
    "finance":  "💰",
    "learning": "📚",
    "home":     "🏠",
    "other":    "📌",
}


def format_tasks_for_prompt() -> str:
    """
    Повернути короткий список відкритих задач для вставки у системний промпт.
    """
    tasks = get_tasks()
    if not tasks:
        return "Відкритих задач немає."

    lines = []
    for t in tasks:
        due_part = f" (до {_fmt_due(t['due'])})" if t.get("due") else ""
        priority = t.get("priority", "other")
        category = t.get("category", "other")
        label = _PRIORITY_LABEL.get(priority, "[ІНШЕ]")
        cat_icon = _CATEGORY_ICON.get(category, "📌")
        lines.append(f"  [{t['id']}] {label} {cat_icon} {t['text']}{due_part}")
    return "Відкриті задачі:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Форматирование для пользователя (Markdown)
# ---------------------------------------------------------------------------

def format_tasks_for_user() -> str:
    """
    Повернути Markdown-список задач для відображення користувачу.
    Групування: пріоритет (А→В→Б→Г), у кожному пріоритеті — іконка категорії.
    """
    tasks = get_tasks()
    if not tasks:
        return "✅ Відкритих задач немає."

    # Групуємо за пріоритетом
    groups = {"goal": [], "habit": [], "routine": [], "other": []}
    for t in tasks:
        p = t.get("priority", "other")
        groups.get(p, groups["other"]).append(t)

    lines = ["*Відкриті задачі:*"]
    order = [
        ("goal",    "🔴 Цілі (А)"),
        ("habit",   "🟢 Звички (В)"),
        ("routine", "🟡 Рутина (Б)"),
        ("other",   "⚪ Інше (Г)"),
    ]
    for priority_key, label in order:
        group = groups[priority_key]
        if not group:
            continue
        lines.append(f"\n*{label}*")
        for t in group:
            due_part = f" _(до {_fmt_due(t['due'])})_" if t.get("due") else ""
            cat_icon = _CATEGORY_ICON.get(t.get("category", "other"), "📌")
            lines.append(f"• {cat_icon} `[{t['id']}]` {t['text']}{due_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Выполнение команд, полученных от Claude
# ---------------------------------------------------------------------------

def execute_commands(commands: list[dict]) -> str:
    """
    Выполнить список JSON-команд, возвращённых Claude.

    Поддерживаемые команды:
        {"action": "add_task", "text": "...", "due": "..."}
        {"action": "done_task", "id": N}

    Returns:
        Строка с результатами выполнения команд (для логирования / уведомления).
    """
    results = []
    for cmd in commands:
        action = cmd.get("action")
        if action == "add_task":
            text = cmd.get("text", "").strip()
            due = cmd.get("due") or None
            priority = cmd.get("priority", "other")
            category = cmd.get("category", "other")
            if text:
                new_id = task_add(text, due, priority, category)
                due_note = f" (до {_fmt_due(due)})" if due else ""
                p_icon = _PRIORITY_ICON.get(priority, "⚪")
                c_icon = _CATEGORY_ICON.get(category, "📌")
                results.append(f"➕ {p_icon}{c_icon} Задачу додано [{new_id}]: {text}{due_note}")
        elif action == "done_task":
            task_id = cmd.get("id")
            if task_id is not None:
                ok = task_done(int(task_id))
                if ok:
                    results.append(f"✅ Задача [{task_id}] закрыта.")
                else:
                    results.append(f"⚠️ Задача [{task_id}] не найдена или уже закрыта.")
        elif action == "update_memory":
            section = cmd.get("section", "").strip()
            content = cmd.get("content", "").strip()
            if section and content:
                update_memory(section, content)
                # обновление памяти — тихое, без уведомления пользователя
        else:
            pass  # неизвестные команды игнорируем тихо

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Быстрое закрытие задачи (для команды /done)
# ---------------------------------------------------------------------------

def close(task_id: int) -> bool:
    """Закрыть задачу по ID. Возвращает True при успехе."""
    return task_done(task_id)


# ---------------------------------------------------------------------------
# Парсинг JSON-команд из ответа Claude
# ---------------------------------------------------------------------------

def parse_commands_from_response(answer: str) -> tuple[str, list[dict]]:
    """
    Витягти JSON-команди з відповіді Claude.
    Шукаємо всі JSON-масиви [...] з полем "action" — незалежно від довжини і переносів.
    Команди прибираються з тексту перед відправкою користувачу.
    """
    all_commands = []
    spans_to_remove = []

    i = 0
    while i < len(answer):
        if answer[i] != '[':
            i += 1
            continue

        # Знаходимо кінець масиву балансуванням дужок
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
                    # запам'ятовуємо позицію для видалення (з пробілами навколо)
                    start = max(0, i - 1) if i > 0 and answer[i-1] == '\n' else i
                    end = j + 2 if j + 1 < len(answer) and answer[j+1] == '\n' else j + 1
                    spans_to_remove.append((start, end))
        except (json.JSONDecodeError, ValueError):
            pass

        i = j + 1

    # Видаляємо знайдені блоки з кінця (щоб не зсувати індекси)
    clean_text = answer
    for start, end in sorted(spans_to_remove, reverse=True):
        clean_text = clean_text[:start] + clean_text[end:]
    clean_text = clean_text.strip()

    return clean_text, all_commands
