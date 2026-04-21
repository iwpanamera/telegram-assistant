"""Тести для agents/task_agent.py — парсер і форматування."""
import pytest
from agents.task_agent import (
    _fmt_due,
    parse_commands_from_response,
)


class TestFmtDue:
    def test_full_iso(self):
        assert _fmt_due("2026-04-19T20:00") == "19 квітня о 20:00"

    def test_with_space(self):
        assert _fmt_due("2026-04-19 20:00") == "19 квітня о 20:00"

    def test_date_only(self):
        assert _fmt_due("2026-04-19") == "19 квітня"

    def test_january(self):
        assert _fmt_due("2026-01-05T09:30") == "5 січня о 09:30"

    def test_december(self):
        assert _fmt_due("2026-12-31T23:59") == "31 грудня о 23:59"

    def test_invalid_passthrough(self):
        # Невідомий формат повертається як є
        assert _fmt_due("not-a-date") == "not-a-date"

    def test_empty(self):
        assert _fmt_due("") == ""

    def test_none(self):
        assert _fmt_due(None) == ""


class TestParseCommandsFromResponse:
    """
    Back-compat: функція лишається для тестів і аварійного fallback,
    хоча основний шлях тепер tool_use.
    """

    def test_no_commands(self):
        text, cmds = parse_commands_from_response("просто текст без команд")
        assert text == "просто текст без команд"
        assert cmds == []

    def test_single_command(self):
        raw = 'Додано!\n[{"action":"add_task","text":"тест","priority":"other","category":"home"}]'
        text, cmds = parse_commands_from_response(raw)
        assert text == "Додано!"
        assert len(cmds) == 1
        assert cmds[0]["action"] == "add_task"

    def test_multiple_commands_in_one_array(self):
        raw = '[{"action":"a1"},{"action":"a2"}]'
        text, cmds = parse_commands_from_response(raw)
        assert len(cmds) == 2
        assert cmds[0]["action"] == "a1"
        assert cmds[1]["action"] == "a2"

    def test_command_in_middle_of_text(self):
        raw = 'ось [{"action":"x"}] результат'
        text, cmds = parse_commands_from_response(raw)
        assert "ось" in text
        assert "результат" in text
        assert cmds[0]["action"] == "x"

    def test_malformed_json_ignored(self):
        raw = 'текст [це не json]'
        text, cmds = parse_commands_from_response(raw)
        # Невалідний JSON має залишитись у тексті, а команд нема
        assert cmds == []

    def test_nested_brackets_in_string(self):
        raw = '[{"action":"update_memory","content":"щось [дужка] інше"}]'
        text, cmds = parse_commands_from_response(raw)
        assert len(cmds) == 1
        assert "[дужка]" in cmds[0]["content"]

    def test_no_action_field_skipped(self):
        raw = '[{"foo":"bar"}]'
        text, cmds = parse_commands_from_response(raw)
        assert cmds == []
