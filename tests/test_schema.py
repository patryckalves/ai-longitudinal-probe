"""Contracts: what a valid record is. No test here touches the network."""
import pytest
from factory import MODEL, canary, record, unavailable
from pydantic import ValidationError
from schema import Block, Canary, Manifest, Outcome, Question, Record, load_questions


def test_valid_record_passes():
    r = Record(**record())
    assert r.outcome == Outcome.ANSWERED
    assert r.block == Block.FACTUAL


def test_tokens_use_in_out_aliases():
    r = Record(**record())
    assert r.tokens.tokens_in == 30
    assert r.tokens.tokens_out == 40
    assert r.model_dump(by_alias=True)["tokens"]["out"] == 40


def test_extra_field_fails_loud():
    with pytest.raises(ValidationError):
        Record(**record(unknown_field="not in the contract"))


def test_day_differs_from_run_id_fails():
    with pytest.raises(ValidationError, match="differs from run_id"):
        Record(**record(day="2026-11-12"))


def test_bad_date_format_fails():
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        Record(**record(run_id="13/11/2026", day="13/11/2026"))


def test_length_requires_truncated_outcome():
    with pytest.raises(ValidationError, match="finish_reason=length"):
        Record(**record(finish_reason="length", outcome="answered"))
    truncated = Record(**record(finish_reason="length", outcome="truncated"))
    assert truncated.outcome == Outcome.TRUNCATED


def test_answered_with_empty_text_fails():
    with pytest.raises(ValidationError, match="empty answer_text"):
        Record(**record(answer_text="   "))


def test_answered_with_non_200_fails():
    with pytest.raises(ValidationError, match="http_status=200"):
        Record(**record(http_status=500))


def test_unavailable_carrying_answer_fails():
    with pytest.raises(ValidationError, match="cannot carry an answer"):
        Record(**unavailable(answer_text="aqui está a resposta"))


def test_free_model_with_cost_fails():
    with pytest.raises(ValidationError, match="with cost > 0"):
        Record(**record(cost_usd=0.001))


def test_model_without_free_tag_may_cost():
    r = Record(**record(model_id="vendor/model-a", cost_usd=0.002))
    assert r.cost_usd == 0.002


def test_draw_must_be_positive():
    with pytest.raises(ValidationError):
        Record(**record(draw=0))


def test_canary_block_requires_canary_slug():
    with pytest.raises(ValidationError, match="canario-"):
        Record(**record(block="K", question_slug="stf-ministros"))


def test_canary_slug_requires_canary_block():
    with pytest.raises(ValidationError, match="requires block K"):
        Record(**record(question_slug="canario-eco"))


def test_valid_canary_passes():
    assert Record(**canary()).block == Block.CANARY


def test_bad_version_format_fails():
    with pytest.raises(ValidationError, match="vN.N.N"):
        Record(**record(instrument_version="1.0"))


def test_manifest_coverage_above_one_fails():
    manifest = {
        "run_id": "2026-11-13", "instrument_version": "v1.0.0",
        "generated_at": "2026-11-13T08:00:00+00:00", "roster": [MODEL], "draws": 5,
        "questions": 14, "canaries_included": True, "records_this_run": 10,
        "records_today": 10, "target_today": 10, "coverage": 1.4,
        "outcomes_this_run": {"answered": 10}, "calls": 10, "duration_s": 60,
        "actions_minutes": 1.0, "bytes_written": 100, "cost_usd": 0.0, "file": "x.jsonl",
    }
    with pytest.raises(ValidationError, match="coverage"):
        Manifest(**manifest)
    manifest["coverage"] = 1.0
    assert Manifest(**manifest).coverage == 1.0


def test_canary_question_without_canary_object_fails():
    with pytest.raises(ValidationError, match="canary"):
        Question(slug="canario-x", block="K", version="v1.0.0", active_since="2026-11-13",
                 text="oi")


def test_question_without_text_fails():
    with pytest.raises(ValidationError, match="without text"):
        Question(slug="x", block="F", version="v1.0.0", active_since="2026-11-13", text="  ")


def test_canary_rejects_unsupported_expected_mode():
    with pytest.raises(ValidationError):
        Canary(expected="regex", value="42")


def test_load_questions_rejects_mixed_versions(tmp_path):
    q1 = Question(slug="q1", block="F", version="v1.0.0", active_since="2026-11-13", text="a?")
    q2 = Question(slug="q2", block="F", version="v1.0.1", active_since="2026-11-13", text="b?")
    path = tmp_path / "questions.jsonl"
    path.write_text(q1.model_dump_json() + "\n" + q2.model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="mixed instrument versions"):
        load_questions(path)
