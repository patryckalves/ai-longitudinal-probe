"""Tests for the manifest summary generator."""
import json

import summarize


def _manifest(run_id: str, records_today: int, calls: int) -> str:
    return json.dumps(
        {
            "run_id": run_id,
            "records_today": records_today,
            "calls": calls,
        }
    )


def test_build_summary_aggregates_distinct_days_and_totals(tmp_path):
    (tmp_path / "gemini").mkdir()
    (tmp_path / "openrouter").mkdir()
    (tmp_path / "gemini" / "2026-11-14.json").write_text(
        _manifest("2026-11-14", 95, 95), encoding="utf-8"
    )
    (tmp_path / "openrouter" / "2026-11-14.json").write_text(
        _manifest("2026-11-14", 380, 380), encoding="utf-8"
    )
    (tmp_path / "openrouter" / "2026-11-15.json").write_text(
        _manifest("2026-11-15", 213, 213), encoding="utf-8"
    )
    (tmp_path / "summary.json").write_text('{"days": 999}', encoding="utf-8")

    summary = summarize.build_summary(tmp_path)
    assert summary == {
        "days": 2,
        "records": 688,
        "calls": 688,
        "last_day": "2026-11-15",
        "providers": ["gemini", "openrouter"],
    }


def test_build_summary_handles_empty_directory(tmp_path):
    assert summarize.build_summary(tmp_path) == {
        "days": 0,
        "records": 0,
        "calls": 0,
        "last_day": None,
        "providers": [],
    }
