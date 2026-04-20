import json
import re
import hashlib
from datetime import datetime, timedelta
from db import task_add, task_done, tasks_open
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


def format_tasks_for_prompt() -> str:
    """
    Вернуть краткий список открытых задач для вставки в системный промпт.
    Если задач нет — вернуть пустую строку.
    """
    tasks = get_tasks()
    if not tasks:
        return "Открытых задач нет."

    lines = []
    for t in tasks:
        due_part = f" (до {_fmt_due(t['due'])})" if t.get("due") else ""
        priority = t.get("priority", "other")
        label = _PRIORITY_LABEL.get(priority, "[ПРОЧЕЕ]")
        lines.append(f"  [{t['id']}] {label} {t['text']}{due_part}")
    return "Открытые задачи:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Форматирование для пользователя (Markdown)
# ---------------------------------------------------------------------------

def format_tasks_for_user() -> str:
    """
    Вернуть отформатированный Markdown-список задач для отображения пользователю.
    """
    tasks = get_tasks()
    if not tasks:
        return "✅ Открытых задач нет."

    # Групуємо за пріоритетом: А → В → Б → Г
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
            lines.append(f"• `[{t['id']}]` {t['text']}{due_part}")
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
            if text:
                new_id = task_add(text, due, priority)
                due_note = f" (до {_fmt_due(due)})" if due else ""
                icon = _PRIORITY_ICON.get(priority, "📌")
                results.append(f"➕ {icon} Задача добавлена [{new_id}]: {text}{due_note}")
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

# Ищем JSON-массивы с action в любом месте ответа
_CMD_PATTERN = re.compile(
    r"\n?\s*(\[(?:\{[^}]*\"action\"[^}]*\}(?:,\s*\{[^}]*\"action\"[^}]*\})*)\])\s*\n?",
    re.DOTALL,
)


def parse_commands_from_response(answer: str) -> tuple[str, list[dict]]:
    """
    Извлечь JSON-команды из ответа Claude (в любом месте, не только в конце).
    Команды убираются из текста перед отправкой пользователю.
    """
    all_commands = []
    clean_text = answer

    for match in _CMD_PATTERN.finditer(answer):
        raw_json = match.group(1)
        try:
            cmds = json.loads(raw_json)
            if not isinstance(cmds, list):
                cmds = [cmds]
            # берём только объекты с полем action
            cmds = [c for c in cmds if isinstance(c, dict) and "action" in c]
            all_commands.extend(cmds)
        except json.JSONDecodeError:
            pass

    # убираем все найденные JSON-блоки из текста
    clean_text = _CMD_PATTERN.sub("", answer).strip()

    return clean_text, all_commands
