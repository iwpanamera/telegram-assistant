import os
import logging
from datetime import datetime
import anthropic
import pytz
from dotenv import load_dotenv

from agents.memory_agent import remember, recall
from agents.task_agent import format_tasks_for_prompt
from agents.memory_loop import read_memory, read_context
from agents.optimization_utils import is_simple_query
from agents.metrics import log_anthropic_usage

load_dotenv()

logger = logging.getLogger(__name__)

# max_retries=3 — вбудований retry з exp backoff у SDK
_client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    max_retries=3,
    timeout=30.0,
)
_MODEL = "claude-haiku-4-5-20251001"
_TZ = pytz.timezone('Europe/Kyiv')

_DAY_NAMES_UA = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]


def _now_kyiv() -> datetime:
    """Поточний час у Києві — надійний спосіб: спочатку UTC, потім astimezone."""
    return datetime.now(pytz.utc).astimezone(_TZ)


# ---------------------------------------------------------------------------
# Tool schemas — Claude викликає ці tools замість JSON-в-тексті
# ---------------------------------------------------------------------------

_CATEGORIES = ["work", "family", "church", "health",
               "finance", "learning", "home", "other"]
_PRIORITIES = ["goal", "habit", "routine", "other"]
_MEMORY_SECTIONS = ["Voice", "Process", "People", "Projects",
                    "Output", "Tools", "Goals"]

TOOLS = [
    {
        "name": "add_task",
        "description": (
            "Додати звичайну задачу. "
            "Якщо користувач не указав дату — НЕ додавай поле due (задача на сьогодні). "
            "Якщо указав конкретну дату/час — передай ISO формат (2026-04-22T17:00). "
            "ВАЖЛИВО: priority=goal якщо задача просуває одну з цілей з памʼяті; "
            "priority=routine для побутових/адміністративних; "
            "priority=other для всього іншого. "
            "НІКОЛИ не став priority=habit тут — для звичок використовуй add_habit. "
            "habit зарезервовано ТІЛЬКИ для щоденних повторюваних дій (пити воду, читати тощо)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "due": {
                    "type": "string",
                    "description": "ISO datetime, напр. 2026-04-25T14:00. ТІЛЬКИ якщо користувач указав дату!",
                },
                "priority": {"type": "string", "enum": _PRIORITIES},
                "category": {"type": "string", "enum": _CATEGORIES},
            },
            "required": ["text", "priority", "category"],
        },
    },
    {
        "name": "add_habit",
        "description": "Додати щоденну звичку (автоматично priority=habit).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "category": {"type": "string", "enum": _CATEGORIES},
            },
            "required": ["text"],
        },
    },
    {
        "name": "add_event",
        "description": (
            "Додати подію — те, що відбудеться у конкретний час "
            "(зустріч, дзвінок, захід, лікар, тренування). "
            "due ОБОВ'ЯЗКОВЕ і у ISO форматі: 2026-04-22T16:00. "
            "Після минулого часу запитаю 'як пройшло?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "due": {"type": "string", "description": "ISO datetime, обов'язкове! 2026-04-22T16:00"},
            },
            "required": ["text", "due"],
        },
    },
    {
        "name": "add_reminder",
        "description": (
            "Додати напоминання — спрацює у вказаний час. "
            "remind_at ОБОВ'ЯЗКОВЕ і у ISO форматі: 2026-04-22T17:00."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "remind_at": {"type": "string", "description": "ISO datetime, обов'язкове! 2026-04-22T17:00"},
            },
            "required": ["text", "remind_at"],
        },
    },
    {
        "name": "done_task",
        "description": "Закрити задачу/подію/привичку по id.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "update_memory",
        "description": (
            "Оновити секцію памʼяті коли дізнався щось нове про користувача. "
            "Цілі — у секції Goals, по сферах."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "enum": _MEMORY_SECTIONS},
                "content": {"type": "string"},
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "browse",
        "description": (
            "Відкрити сайт або знайти в інтернеті. "
            "Вказуй url для прямого переходу або query для пошуку. "
            "Перед викликом — скажи користувачу 'зараз перевірю...'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "query": {"type": "string"},
                "task": {
                    "type": "string",
                    "description": "Що треба знайти на сторінці",
                },
            },
            "required": ["task"],
        },
    },
]


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """Ти — особистий ІІ-асистент користувача в Telegram.
Допомагаєш планувати справи, відповідаєш на запитання, ведеш задачі й напоминання.
Завжди відповідай українською мовою — незалежно від мови повідомлення.

Стиль: коротко і природно. Без зірочок, markdown, заголовків. Максимум 3-4 речення.
Емоджі природно: 🎯 ціль, ⚡ звичка, 🔄 рутина, 📌 інше.

## Контекст про користувача
{context_block}

## Що ти вже знаєш (памʼять)
{memory_block}

{tasks_block}

## Коли що використовувати
- Задача vs подія: подія — конкретне місце/час (зустріч, лікар). Задача — просто зробити.
- Пріоритет: goal = просуває одну з цілей зі секції Goals; habit = щоденна звичка;
  routine = побут/адмін; other = інше.
- Browse: актуальні ціни, графік, контакти, курс, погода.
  Для погоди — використовуй query, напр. "погода Київ".
  Для відомих сайтів (bank.gov.ua, rozetka) — url напряму.

## ВАЖЛИВО: Дати і часи
- Якщо користувач просить додати задачу БЕЗ указання дати — це задача на СЬОГОДНІ (без поля due)
- Якщо користувач говорить "завтра" / "вдень" / "вечером" / "о 17:00" — тоді ставиш конкретну дату/час
- Дату/час передаєш як ISO: "2026-04-22T17:00" (SAMEDAY якщо не указано, не NEXTDAY)
- "скоро" / "невдовзі" = без дати (сьогодні)

Використовуй tools для дій. Говори природно, а дії виконуй окремо через tool calls.

## Поточна дата і час
{datetime_now}
"""

_SYSTEM_SIMPLE = """Ти — особистий ІІ-асистент. Відповідай коротко, українською.
Без markdown. Для дій — використовуй tools.

## Поточна дата і час
{datetime_now}
"""


def _build_system_prompt(simple: bool = False) -> tuple:
    """Побудувати system prompt. Returns (text, is_full_context)."""
    if simple:
        now_kyiv = _now_kyiv()
        day_name = _DAY_NAMES_UA[now_kyiv.weekday()]
        now = f"{now_kyiv.strftime('%d.%m.%Y %H:%M')} ({day_name})"
        logger.info(f"[TIMEZONE DEBUG] Simple prompt: UTC={datetime.now(pytz.utc).strftime('%d.%m %H:%M')} → Kyiv={now}")
        return _SYSTEM_SIMPLE.format(datetime_now=now), False

    tasks_block = format_tasks_for_prompt()
    now_kyiv = _now_kyiv()
    day_name = _DAY_NAMES_UA[now_kyiv.weekday()]
    now = f"{now_kyiv.strftime('%d.%m.%Y %H:%M')} ({day_name})"
    logger.info(f"[TIMEZONE DEBUG] Full prompt: UTC={datetime.now(pytz.utc).strftime('%d.%m %H:%M')} → Kyiv={now}")
    memory = read_memory() or "(поки порожньо)"
    context = read_context() or "(не заповнено)"

    full_prompt = _SYSTEM_TEMPLATE.format(
        tasks_block=tasks_block,
        datetime_now=now,
        memory_block=memory,
        context_block=context,
    )
    return full_prompt, True


# ---------------------------------------------------------------------------
# Основна функція think
# ---------------------------------------------------------------------------

def think(user_text: str) -> tuple[str, list[dict]]:
    """
    Прийняти текст користувача, повернути (text, tool_uses).

    tool_uses — список {"action": str, ...params}, готовий до execute_commands.

    Використовує Tool Use API — структуровані tool calls замість JSON-парсера.
    Відповіді кешуються (prompt caching) для повного контексту.
    """
    remember("user", user_text)

    is_simple = is_simple_query(user_text)
    history = recall(smart=not is_simple)
    messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history
    ]

    system_prompt, full_context = _build_system_prompt(simple=is_simple)

    # prompt caching: для повного контексту використовуємо ephemeral cache
    if full_context:
        system_param = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]
    else:
        system_param = system_prompt

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=system_param,
        tools=TOOLS,
        messages=messages,
    )
    log_anthropic_usage(response, label="think")

    text_parts = []
    tool_uses = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            tool_uses.append({"action": block.name, **(block.input or {})})

    text = "\n".join(p for p in text_parts if p).strip()

    # Зберігаємо в історію навіть якщо тільки tool calls
    if text:
        remember("assistant", text)
    elif tool_uses:
        summary = ", ".join(t["action"] for t in tool_uses)
        remember("assistant", f"[дії: {summary}]")

    return text, tool_uses


def think_browse_result(original_query: str, browse_result: str) -> str:
    """
    Сформувати відповідь на основі результату browse.
    Не зберігає запит у основну історію, тільки фінальну відповідь.
    """
    system = (
        "Ти — особистий ІІ-асистент. "
        "На основі даних з інтернету дай корисну відповідь на запит користувача. "
        "Відповідай коротко і чітко, українською. Без markdown. "
        "Якщо даних недостатньо — скажи чесно."
    )
    content = f"Запит: {original_query}\n\nДані з інтернету:\n{browse_result}"

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    log_anthropic_usage(response, label="browse_result")

    answer = response.content[0].text.strip()
    remember("assistant", answer)
    return answer
