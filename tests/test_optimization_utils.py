"""Тести для agents/optimization_utils.py — чисті функції без IO."""
import pytest
from agents.optimization_utils import (
    is_simple_query,
    count_tokens_estimate,
    should_transcribe_voice,
    should_summarize_transcript,
    truncate_history_smart,
)


class TestIsSimpleQuery:
    def test_short_greeting(self):
        assert is_simple_query("привет") is True

    def test_time_question(self):
        assert is_simple_query("что сейчас время") is True

    def test_command(self):
        assert is_simple_query("/start") is True

    def test_long_complex_query(self):
        text = "напиши мені розгорнутий план на тиждень з урахуванням цілей"
        assert is_simple_query(text) is False

    def test_empty_string(self):
        assert is_simple_query("") is False


class TestCountTokensEstimate:
    def test_empty(self):
        assert count_tokens_estimate("") == 0

    def test_short_text(self):
        # 9 chars / 3 = 3 tokens
        assert count_tokens_estimate("hello wor") == 3

    def test_proportional(self):
        t1 = count_tokens_estimate("a" * 300)
        t2 = count_tokens_estimate("a" * 600)
        assert t2 == 2 * t1


class TestShouldTranscribeVoice:
    def test_too_short(self):
        assert should_transcribe_voice(1.5) is False

    def test_borderline(self):
        assert should_transcribe_voice(2.0) is True

    def test_long(self):
        assert should_transcribe_voice(30.0) is True


class TestShouldSummarizeTranscript:
    def test_short(self):
        assert should_summarize_transcript("hello world") is False

    def test_exactly_500_words(self):
        text = " ".join(["word"] * 500)
        assert should_summarize_transcript(text) is False

    def test_long(self):
        text = " ".join(["word"] * 600)
        assert should_summarize_transcript(text) is True


class TestTruncateHistorySmart:
    def test_empty(self):
        assert truncate_history_smart([]) == []

    def test_fits_returns_all(self):
        msgs = [
            {"role": "user", "content": "hi", "ts": "2026-04-20T10:00:00"},
            {"role": "assistant", "content": "привіт", "ts": "2026-04-20T10:00:01"},
        ]
        result = truncate_history_smart(msgs, max_tokens=1000)
        assert len(result) == 2

    def test_preserves_last_five_when_overflow(self):
        msgs = [
            {"role": "user", "content": "x" * 3000, "ts": "2026-04-20T10:00:00"}
            for _ in range(20)
        ]
        result = truncate_history_smart(msgs, max_tokens=500)
        # останні 5 завжди мають бути
        assert len(result) >= 5
