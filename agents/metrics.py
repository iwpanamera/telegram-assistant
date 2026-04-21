"""
metrics.py — логування токенів і оцінка вартості викликів Claude/Groq.

Ціни Haiku 4.5 (на квітень 2026):
  - вхідні:   $0.80 / 1M
  - вихідні:  $4.00 / 1M
  - cache read:  $0.08 / 1M
  - cache write: $1.00 / 1M

Ціни Whisper Large v3 Turbo (Groq): ~$0.04 / годину аудіо
"""
import logging

logger = logging.getLogger(__name__)

_HAIKU_IN   = 0.80 / 1_000_000
_HAIKU_OUT  = 4.00 / 1_000_000
_HAIKU_CR   = 0.08 / 1_000_000   # cache read
_HAIKU_CW   = 1.00 / 1_000_000   # cache write

# Кумулятивна статистика за runtime процесу
_stats = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cost_usd": 0.0,
}


def log_anthropic_usage(response, label: str = "claude"):
    """
    Залогувати usage з Anthropic response і оновити кумулятивну статистику.

    Повертає словник з лічильниками цього виклику.
    """
    try:
        u = response.usage
        inp   = getattr(u, "input_tokens", 0) or 0
        out   = getattr(u, "output_tokens", 0) or 0
        cread = getattr(u, "cache_read_input_tokens", 0) or 0
        cwrite = getattr(u, "cache_creation_input_tokens", 0) or 0

        cost = (
            inp * _HAIKU_IN +
            out * _HAIKU_OUT +
            cread * _HAIKU_CR +
            cwrite * _HAIKU_CW
        )

        _stats["calls"] += 1
        _stats["input_tokens"] += inp
        _stats["output_tokens"] += out
        _stats["cache_read_tokens"] += cread
        _stats["cache_write_tokens"] += cwrite
        _stats["cost_usd"] += cost

        logger.info(
            "[%s] tokens in=%d out=%d cache_r=%d cache_w=%d "
            "cost=$%.5f (total=$%.3f, calls=%d)",
            label, inp, out, cread, cwrite,
            cost, _stats["cost_usd"], _stats["calls"]
        )
        return {
            "input_tokens": inp, "output_tokens": out,
            "cache_read_tokens": cread, "cache_write_tokens": cwrite,
            "cost_usd": cost,
        }
    except Exception as e:
        logger.debug("log_anthropic_usage failed: %s", e)
        return {}


def get_stats() -> dict:
    """Повернути кумулятивну статистику за час життя процесу."""
    return dict(_stats)


def log_stats_summary():
    """Вивести зведення в лог (корисно при shutdown)."""
    logger.info(
        "📊 Підсумок сесії: calls=%d, tokens_in=%d, tokens_out=%d, "
        "cache_r=%d, cache_w=%d, cost=$%.4f",
        _stats["calls"], _stats["input_tokens"], _stats["output_tokens"],
        _stats["cache_read_tokens"], _stats["cache_write_tokens"],
        _stats["cost_usd"],
    )
