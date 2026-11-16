"""Factory of valid records — used by the tests. Never produces real data."""
import hashlib

TEXT = "Onze ministros compõem o Supremo Tribunal Federal, conforme o artigo 101."
MODEL = "vendor/model-a:free"


def record(**over) -> dict:
    text = over.pop("answer_text", TEXT)
    base = {
        "id": "00000000-0000-4000-8000-000000000000",
        "run_id": "2026-11-13",
        "day": "2026-11-13",
        "collected_at": "2026-11-13T08:00:00+00:00",
        "question_slug": "stf-ministros",
        "question_text": "Quantos ministros compõem o Supremo Tribunal Federal atualmente?",
        "instrument_version": "v1.0.0",
        "block": "F",
        "draw": 1,
        "model_id": MODEL,
        "canonical_slug": "model-a",
        "declared_version": "model-a",
        "provider": "Vendor",
        "route": "openrouter.ai",
        "params": {"temperature": 0.0, "max_tokens": 1500, "draw": 1},
        "system_prompt": None,
        "answer_text": text,
        "answer_chars": len(text),
        "answer_sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
        "outcome": "answered",
        "error_type": None,
        "http_status": 200,
        "finish_reason": "stop",
        "latency_ms": 1200,
        "tokens": {"in": 30, "out": 40, "reasoning": 0},
        "cost_usd": 0.0,
    }
    base.update(over)
    final_text = base["answer_text"]
    base["answer_chars"] = len(final_text)
    base["answer_sha256"] = hashlib.sha256(final_text.encode()).hexdigest()[:16]
    return base


def canary(slug="canario-aritmetica", model=MODEL, draw=1, answer_text="42", **over) -> dict:
    return record(
        question_slug=slug,
        question_text="Quanto é 17 + 25? Responda apenas o número.",
        block="K",
        model_id=model,
        draw=draw,
        answer_text=answer_text,
        **over,
    )


def unavailable(**over) -> dict:
    defaults = {
        "answer_text": "",
        "outcome": "unavailable",
        "http_status": 429,
        "finish_reason": None,
        "tokens": {"in": None, "out": None, "reasoning": None},
        "error_type": "http_429: rate limited",
    }
    defaults.update(over)
    return record(**defaults)
