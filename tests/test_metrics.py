"""Тести для agents/metrics.py — логування token usage без реальних API викликів."""
from types import SimpleNamespace

from agents.metrics import log_anthropic_usage, get_stats


def _fake_response(inp, out, cread=0, cwrite=0):
    """Імітувати Anthropic response з потрібним usage."""
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=cread,
            cache_creation_input_tokens=cwrite,
        )
    )


class TestLogAnthropicUsage:
    def test_basic_accumulation(self):
        before = get_stats()["calls"]
        log_anthropic_usage(_fake_response(100, 50), label="test")
        after = get_stats()
        assert after["calls"] == before + 1
        assert after["input_tokens"] >= 100
        assert after["output_tokens"] >= 50

    def test_cost_estimate_positive(self):
        result = log_anthropic_usage(_fake_response(1000, 500), label="test")
        assert result["cost_usd"] > 0

    def test_cache_tokens_tracked(self):
        result = log_anthropic_usage(
            _fake_response(100, 50, cread=1000, cwrite=500),
            label="test",
        )
        assert result["cache_read_tokens"] == 1000
        assert result["cache_write_tokens"] == 500

    def test_missing_usage_does_not_crash(self):
        bad = SimpleNamespace()  # без usage
        result = log_anthropic_usage(bad, label="test")
        assert result == {}
