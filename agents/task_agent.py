import json
import re
from datetime import datetime
from db import task_add, task_done, tasks_open

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

def get_tasks() -> list[dict]:
    """Вернуть список открытых задач из БД."""
    return tasks_open()


# ---------------------------------------------------------------------------
# Форматирование для системного промпта
# ---------------------------------------------------------------------------

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
        lines.append(f"  [{t['id']}] {t['text']}{due_part}")
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

    lines = ["*Открытые задачи:*"]
    for t in tasks:
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
            if text:
                new_id = task_add(text, due)
                due_note = f" (до {_fmt_due(due)})" if due else ""
                results.append(f"➕ Задача добавлена [{new_id}]: {text}{due_note}")
        elif action == "done_task":
            task_id = cmd.get("id")
            if task_id is not None:
                ok = task_done(int(task_id))
                if ok:
                    results.append(f"✅ Задача [{task_id}] закрыта.")
                else:
                    results.append(f"⚠️ Задача [{task_id}] не найдена или уже закрыта.")
        else:
            results.append(f"⚠️ Неизвестная команда: {action}")

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

_CMD_PATTERN = re.compile(
    r"\n?\s*(\[(?:\{.*?\}(?:,\s*)?)+\])\s*$",
    re.DOTALL,
)


def parse_commands_from_response(answer: str) -> tuple[str, list[dict]]:
    """
    Извлечь JSON-команды из конца ответа Claude.

    Claude добавляет команды в конец ответа на отдельной строке в виде
    JSON-массива, например:
        [{"action":"add_task","text":"Купить молоко","due":"2024-12-01"}]

    Returns:
        (чистый_текст, список_команд)
        Если команд нет — список пустой.
    """
    match = _CMD_PATTERN.search(answer)
    if not match:
        return answer.strip(), []

    raw_json = match.group(1)
    clean_text = answer[: match.start()].strip()

    try:
        commands = json.loads(raw_json)
        if not isinstance(commands, list):
            commands = [commands]
    except json.JSONDecodeError:
        # Если не удалось распарсить — возвращаем весь текст без изменений
        return answer.strip(), []

    return clean_text, commands
