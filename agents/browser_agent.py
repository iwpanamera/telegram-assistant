"""
browser_agent.py — веб-браузер агент для АИ АС

Стратегія fetch (швидкість → надійність):
  1. aiohttp (простий HTTP, ~0.5-2 сек) — для більшості сайтів і API
  2. Playwright fallback (~8-15 сек) — для JS-важких сторінок

Команда Claude для активації:
  [{"action": "browse", "url": "https://...", "task": "знайди ціну"}]
  [{"action": "browse", "query": "iPhone 16 ціна", "task": "знайди найкращу ціну"}]
"""

import logging
import os
import urllib.parse

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_MODEL = "claude-haiku-4-5-20251001"

# Максимальна кількість символів тексту для Claude
_MAX_PAGE_CHARS = 6000

# Мінімальна довжина "корисного" тексту — якщо менше, пробуємо Playwright
_MIN_USEFUL_CHARS = 300

# Таймаут Playwright (мс)
_PLAYWRIGHT_TIMEOUT_MS = 30_000

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "uk,en-US;q=0.8,en;q=0.6",
}


# ---------------------------------------------------------------------------
# Утиліти
# ---------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    """Прибрати зайві пробіли і порожні рядки."""
    lines = [line.strip() for line in raw.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned[:_MAX_PAGE_CHARS]


def _html_to_text(html: str) -> str:
    """Витягти текст з HTML через BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav",
                         "footer", "header", "aside", "iframe", "svg", "form"]):
            tag.decompose()
        return _clean_text(soup.get_text(separator="\n"))
    except ImportError:
        # Fallback: прибираємо HTML-теги регексом
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        return _clean_text(text)


# ---------------------------------------------------------------------------
# Два методи fetch
# ---------------------------------------------------------------------------

async def _fetch_via_http(url: str) -> str:
    """
    Швидкий fetch через aiohttp (~0.5-2 сек).
    Повертає текст сторінки або порожній рядок якщо не вийшло.
    """
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(headers=_HTTP_HEADERS) as session:
            async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.debug("_fetch_via_http: status=%d for %s", resp.status, url)
                    return ""
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type:
                    return await resp.text()  # API повертає JSON — передаємо напряму
                html = await resp.text(errors="replace")
        text = _html_to_text(html)
        logger.info("_fetch_via_http: OK length=%d for %s", len(text), url)
        return text
    except Exception as e:
        logger.debug("_fetch_via_http failed for %s: %s", url, e)
        return ""


async def _fetch_via_playwright(url: str) -> str:
    """
    Важкий fallback через Playwright headless Chromium (~8-15 сек).
    Для JS-важких сторінок де aiohttp не дає корисного контенту.
    """
    try:
        from playwright.async_api import async_playwright
        logger.info("_fetch_via_playwright: launching Chromium for %s", url)
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            ctx = await browser.new_context(
                user_agent=_HTTP_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=_PLAYWRIGHT_TIMEOUT_MS,
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                raw = await page.evaluate("""
                    () => {
                        ['script','style','noscript','nav','footer',
                         'header','aside','iframe','svg','form'].forEach(tag => {
                            document.querySelectorAll(tag).forEach(el => el.remove());
                        });
                        return document.body ? document.body.innerText : '';
                    }
                """)
            except Exception as e:
                logger.warning("_fetch_via_playwright page error for %s: %s", url, e)
                raw = ""
            finally:
                await browser.close()
        text = _clean_text(raw)
        logger.info("_fetch_via_playwright: OK length=%d for %s", len(text), url)
        return text
    except ImportError:
        logger.error("Playwright not installed!")
        return ""
    except Exception as e:
        logger.error("_fetch_via_playwright error for %s: %s", url, e, exc_info=True)
        return ""


async def _fetch_page_text(url: str) -> str:
    """
    Головна функція fetch: спочатку aiohttp, fallback на Playwright.
    Повертає текст або рядок з помилкою.
    """
    # Спроба 1: швидкий HTTP
    text = await _fetch_via_http(url)
    if len(text) >= _MIN_USEFUL_CHARS:
        return text

    # Спроба 2: Playwright (JS-рендеринг)
    logger.info("_fetch_page_text: HTTP gave %d chars, trying Playwright", len(text))
    pw_text = await _fetch_via_playwright(url)
    if pw_text:
        return pw_text

    # Обидва провалились
    if text:
        return text  # хоч щось
    return "Помилка: не вдалося отримати вміст сторінки."


# ---------------------------------------------------------------------------
# Claude-аналіз вмісту
# ---------------------------------------------------------------------------

def _analyze_with_claude(task: str, page_text: str, url: str = "") -> str:
    """Передати вміст сторінки в Claude Haiku і отримати коротку відповідь."""
    if not page_text or "Помилка:" in page_text:
        return page_text or "Не вдалося отримати вміст сторінки."

    source_hint = f" (джерело: {url})" if url else ""
    prompt = (
        f"Задача: {task}\n\n"
        f"Вміст сторінки{source_hint}:\n{page_text}\n\n"
        "Витягни тільки інформацію, потрібну для виконання задачі. "
        "Відповідай коротко і чітко, українською мовою. "
        "Без markdown, без зірочок, без заголовків."
    )
    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error("Claude analyze error: %s", e)
        return f"Не вдалося проаналізувати вміст: {e}"


# ---------------------------------------------------------------------------
# Публічні async функції
# ---------------------------------------------------------------------------

async def browse_url(url: str, task: str) -> str:
    """Відкрити URL і витягти інформацію по задачі."""
    logger.info("browse_url: url=%s task=%s", url, task)
    page_text = await _fetch_page_text(url)
    return _analyze_with_claude(task, page_text, url)


_WEATHER_KEYWORDS = (
    "погода", "weather", "температур", "прогноз",
    "forecast", "дощ", "сніг", "хмар",
)


def _is_weather_query(query: str) -> str | None:
    """
    Якщо запит про погоду — повернути назву міста для wttr.in, інакше None.
    """
    q = query.lower()
    if not any(kw in q for kw in _WEATHER_KEYWORDS):
        return None
    stop_words = {
        "погода", "weather", "прогноз", "forecast",
        "в", "у", "на", "сьогодні", "today", "зараз", "now",
        "поточна", "current",
    }
    words = [w for w in query.split() if w.lower() not in stop_words]
    return "+".join(words) if words else "Kyiv"


async def search_web(query: str, task: str) -> str:
    """
    Пошук в інтернеті.
    - Погодні запити → wttr.in (надійно, без браузера)
    - Решта → DuckDuckGo (aiohttp → Playwright fallback)
    """
    logger.info("search_web: query=%s", query)

    # Погода → wttr.in
    city = _is_weather_query(query)
    if city:
        wttr_url = f"https://wttr.in/{city}?format=4"
        logger.info("search_web: weather → wttr.in city=%s", city)
        text = await _fetch_via_http(wttr_url)
        logger.info("search_web: wttr.in length=%d preview=%r", len(text), text[:100])
        if text and len(text) > 5:
            return text
        # Fallback: повна сторінка wttr.in
        text = await _fetch_page_text(f"https://wttr.in/{city}")
        return _analyze_with_claude(task or query, text, f"https://wttr.in/{city}")

    # Загальний пошук → DuckDuckGo
    encoded = urllib.parse.quote_plus(query)
    ddg_url = f"https://html.duckduckgo.com/html/?q={encoded}"
    text = await _fetch_page_text(ddg_url)
    logger.info("search_web: DDG length=%d", len(text))
    return _analyze_with_claude(task or query, text, ddg_url)


async def execute_browse(cmd: dict) -> str:
    """
    Виконати команду browse від Claude.

    Формати:
        {"action": "browse", "url": "https://...", "task": "..."}
        {"action": "browse", "query": "пошук", "task": "..."}
    """
    logger.info("execute_browse: cmd=%s", cmd)

    url   = (cmd.get("url")   or "").strip()
    query = (cmd.get("query") or "").strip()
    task  = (cmd.get("task")  or "витягни корисну інформацію").strip()

    if url:
        result = await browse_url(url, task)
    elif query:
        result = await search_web(query, task)
    else:
        result = "Потрібно вказати url або query для команди browse."

    logger.info("execute_browse: result preview: %s", result[:300])
    return result
