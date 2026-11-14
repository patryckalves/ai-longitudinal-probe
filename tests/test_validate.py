"""Data quality and smoke test — through validate.py's real CLI."""
import json
import pathlib
import subprocess
import sys

from factory import canary, record, unavailable
from validate import today_brt

ROOT = pathlib.Path(__file__).resolve().parent.parent
VALIDATE = ROOT / "scripts" / "validate.py"


def run(path: pathlib.Path, *extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATE), "--file", str(path), *extra],
        capture_output=True, text=True, cwd=str(ROOT),
    )


def write(tmp_path: pathlib.Path, records) -> pathlib.Path:
    path = tmp_path / "2026-11-13.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                    encoding="utf-8")
    return path


def test_healthy_day_passes(tmp_path):
    records = [record(), record(draw=2, answer_text="Onze ministros."),
               canary(), canary(draw=2), unavailable(draw=3)]
    result = run(write(tmp_path, records))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no duplicates" in result.stdout


def test_duplicate_cell_fails(tmp_path):
    result = run(write(tmp_path, [record(), record()]))
    assert result.returncode == 1
    assert "duplicate cell" in result.stdout


def test_two_days_in_one_file_fails(tmp_path):
    records = [record(), record(run_id="2026-11-12", day="2026-11-12")]
    result = run(write(tmp_path, records))
    assert result.returncode == 1
    assert "mixes 2 days" in result.stdout


def test_malformed_record_fails_with_line_number(tmp_path):
    path = tmp_path / "2026-11-13.jsonl"
    good = record()
    bad = record(draw=2, finish_reason="length")  # length requires truncated
    path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
    result = run(path)
    assert result.returncode == 1
    assert "2026-11-13.jsonl:2" in result.stdout


def test_broken_canary_fails_the_job(tmp_path):
    """A model that fails the canary points at the plumbing, not at behaviour."""
    records = [record(), record(draw=2, answer_text="Onze."),
               canary(answer_text="Desculpe, não posso repetir."),
               canary(draw=2, answer_text="Não vou repetir isso.")]
    result = run(write(tmp_path, records))
    assert result.returncode == 1
    assert "canary:" in result.stdout


def test_partial_canary_is_a_warning(tmp_path):
    records = [record(), record(draw=2, answer_text="Onze."),
               canary(answer_text="42"), canary(draw=2, answer_text="quarenta e dois")]
    result = run(write(tmp_path, records))
    assert result.returncode == 0, result.stdout
    assert "50%" in result.stdout


def test_no_answer_fails(tmp_path):
    result = run(write(tmp_path, [unavailable()]))
    assert result.returncode == 1
    assert "instrument broken" in result.stdout


def test_version_mismatch_on_today_fails(tmp_path):
    """Battery swapped mid-series: today's collection must fail, not start a new series."""
    today = today_brt()
    records = [record(run_id=today, day=today, instrument_version="v0.9.0"),
               canary(run_id=today, day=today, instrument_version="v0.9.0")]
    result = run(write(tmp_path, records))
    assert result.returncode == 1
    assert "differs from current battery" in result.stdout


def test_version_mismatch_on_old_day_only_warns(tmp_path):
    records = [record(run_id="2026-01-01", day="2026-01-01", instrument_version="v0.9.0"),
               canary(run_id="2026-01-01", day="2026-01-01", instrument_version="v0.9.0")]
    result = run(write(tmp_path, records))
    assert result.returncode == 0, result.stdout
    assert "historical file" in result.stdout


def test_two_versions_in_one_file_fails(tmp_path):
    records = [record(instrument_version="v0.9.0"), canary()]
    result = run(write(tmp_path, records))
    assert result.returncode == 1
    assert "mixes instrument versions" in result.stdout


def test_identical_answers_across_draws_warn_about_cache(tmp_path):
    """Same answer byte for byte on different draws: possible provider cache."""
    records = [record(draw=1), record(draw=2), canary()]
    result = run(write(tmp_path, records))
    assert result.returncode == 0
    assert "provider cache" in result.stdout


def test_missing_file_fails(tmp_path):
    result = run(tmp_path / "does-not-exist.jsonl")
    assert result.returncode == 1
    assert "file not found" in result.stdout
