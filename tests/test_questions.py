"""The published battery is a contract: if it breaks, the series dies."""
import pathlib

from schema import Block, load_questions

QUESTIONS = pathlib.Path(__file__).resolve().parent.parent / "questions.jsonl"


def test_battery_loads_and_validates():
    assert len(load_questions(QUESTIONS)) == 19


def test_expected_canaries():
    items = load_questions(QUESTIONS)
    canaries = [q for q in items if q.block == Block.CANARY]
    assert len(canaries) == 4
    assert {q.slug for q in canaries} == {
        "canario-eco",
        "canario-aritmetica",
        "canario-formato",
        "canario-premissa-falsa",
    }


def test_uniform_version():
    assert {q.version for q in load_questions(QUESTIONS)} == {"v1.0.0"}


def test_expected_blocks():
    counts = {}
    for q in load_questions(QUESTIONS):
        counts[q.block] = counts.get(q.block, 0) + 1
    assert counts[Block.FACTUAL] == 8
    assert counts[Block.GENERIC] == 4
    assert counts[Block.CALIBRATION] == 3
    assert counts[Block.CANARY] == 4


def test_canaries_point_to_something_checkable():
    for q in load_questions(QUESTIONS):
        if q.canary is not None:
            assert q.canary.value.strip()
            assert q.canary.expected == "contains"


def test_expiring_question_has_a_future_date():
    for q in load_questions(QUESTIONS):
        if q.expires_at:
            assert q.expires_at > q.active_since
