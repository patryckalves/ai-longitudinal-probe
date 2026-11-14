"""Collector functions — tested without network, with doubles."""
import hashlib
import io
import json
import subprocess
import urllib.error

import collect
import pytest
from factory import MODEL, record
from pydantic import ValidationError
from schema import Outcome, Question

QUESTION = Question(
    slug="stf-ministros", block="F", version="v1.0.0", active_since="2026-11-13",
    text="Quantos ministros compõem o STF?",
)


def test_classify_outcome():
    assert collect.classify_outcome("length", "cut") == Outcome.TRUNCATED
    assert collect.classify_outcome("length", "") == Outcome.TRUNCATED
    assert collect.classify_outcome("stop", "answer") == Outcome.ANSWERED
    assert collect.classify_outcome("stop", "  ") == Outcome.NOT_EVALUATED
    assert collect.classify_outcome(None, "") == Outcome.NOT_EVALUATED


def test_already_collected_counts_cells_and_skips_garbage(tmp_path):
    path = tmp_path / "day.jsonl"
    path.write_text(
        json.dumps(record()) + "\n"
        + "{corrupted line\n"
        + json.dumps(record(draw=2)) + "\n"
        + "\n",
        encoding="utf-8",
    )
    done = collect.already_collected(path)
    assert done == {
        ("2026-11-13", "stf-ministros", MODEL, 1),
        ("2026-11-13", "stf-ministros", MODEL, 2),
    }


def test_already_collected_missing_file(tmp_path):
    assert collect.already_collected(tmp_path / "nothing.jsonl") == set()


def _result(text="Onze ministros.", outcome=Outcome.ANSWERED, http=200, finish="stop"):
    return {
        "answer_text": text, "outcome": outcome, "error_type": None, "http_status": http,
        "latency_ms": 900, "tokens": {"in": 10, "out": 20, "reasoning": 0}, "cost_usd": 0.0,
        "declared_version": "model-a", "provider": "Vendor", "finish_reason": finish,
    }


def test_build_record_keeps_alias_and_validates():
    built = collect.build_record(
        run_id="2026-11-13", question=QUESTION, model=MODEL, draw=3, result=_result()
    )
    assert built["tokens"]["out"] == 20
    assert built["draw"] == 3
    assert built["params"]["draw"] == 3
    assert built["block"] == "F"
    assert built["params"]["temperature"] is None
    assert built["answer_chars"] == len("Onze ministros.")


def test_build_record_rejects_incoherence():
    """A collector writing answered with http 500 is a bug — it must blow up here."""
    with pytest.raises(ValidationError):
        collect.build_record(
            run_id="2026-11-13", question=QUESTION, model=MODEL, draw=1,
            result=_result(http=500),
        )


def test_build_record_truncated_without_text_passes():
    built = collect.build_record(
        run_id="2026-11-13", question=QUESTION, model=MODEL, draw=1,
        result=_result(text="", outcome=Outcome.TRUNCATED, finish="length"),
    )
    assert built["outcome"] == "truncated"


def test_today_brt_format():
    today = collect.today_brt()
    assert len(today) == 10 and today[4] == "-"


def test_load_api_key_prefers_environment(monkeypatch):
    monkeypatch.setenv("PROBE_API_KEY", "from-env")
    monkeypatch.setattr(collect, "_keychain_lookup", lambda service: "from-keychain")
    assert collect.load_api_key() == "from-env"


def test_load_api_key_falls_back_to_keychain(monkeypatch):
    monkeypatch.delenv("PROBE_API_KEY", raising=False)
    monkeypatch.setattr(collect, "_keychain_lookup", lambda service: "from-keychain")
    assert collect.load_api_key() == "from-keychain"


def test_load_api_key_uses_configured_service(monkeypatch):
    seen = {}

    def fake_lookup(service):
        seen["service"] = service
        return None

    monkeypatch.delenv("PROBE_API_KEY", raising=False)
    monkeypatch.setenv("PROBE_KEYCHAIN_SERVICE", "OTHER_SERVICE")
    monkeypatch.setattr(collect, "_keychain_lookup", fake_lookup)
    with pytest.raises(RuntimeError):
        collect.load_api_key()
    assert seen["service"] == "OTHER_SERVICE"


def test_load_api_key_missing_raises(monkeypatch):
    monkeypatch.delenv("PROBE_API_KEY", raising=False)
    monkeypatch.setattr(collect, "_keychain_lookup", lambda service: None)
    with pytest.raises(RuntimeError, match="Keychain"):
        collect.load_api_key()


def test_keychain_lookup_skips_non_darwin(monkeypatch):
    monkeypatch.setattr(collect.sys, "platform", "linux")
    monkeypatch.setattr(
        collect.subprocess, "run",
        lambda *a, **k: pytest.fail("security must not be called off macOS"),
    )
    assert collect._keychain_lookup("PROBE_API_KEY") is None


def test_keychain_lookup_returns_none_when_entry_absent(monkeypatch):
    monkeypatch.setattr(collect.sys, "platform", "darwin")

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(44, "security")

    monkeypatch.setattr(collect.subprocess, "run", boom)
    assert collect._keychain_lookup("PROBE_API_KEY") is None


def test_keychain_lookup_strips_output(monkeypatch):
    monkeypatch.setattr(collect.sys, "platform", "darwin")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="  sk-test-123\n", stderr="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    assert collect._keychain_lookup("PROBE_API_KEY") == "sk-test-123"


def test_call_model_captures_provenance_fields(monkeypatch):
    monkeypatch.setattr(
        collect,
        "_post",
        lambda key, model, text: {
            "id": "gen-123",
            "model": "vendor/model-a-20261101",
            "provider": "Vendor",
            "system_fingerprint": "fp_abc123",
            "choices": [{"message": {"content": "Onze."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0025},
        },
    )
    res = collect.call_model("k", "vendor/model-a", "Quantos?")
    assert res["outcome"] == Outcome.ANSWERED
    assert res["generation_id"] == "gen-123"
    assert res["system_fingerprint"] == "fp_abc123"
    assert res["cost_usd"] == 0.0025


def test_call_model_retries_429_and_recovers(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)

    def flaky(key, model, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                "https://example.com", 429, "Too Many Requests", {}, io.BytesIO(b"rate limit")
            )
        return {
            "choices": [{"message": {"content": "42"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }

    monkeypatch.setattr(collect, "_post", flaky)
    res = collect.call_model("k", "m", "17+25?")
    assert calls["n"] == 2
    assert res["outcome"] == Outcome.ANSWERED
    assert res["answer_text"] == "42"


def test_call_model_400_is_rejected_without_retry(monkeypatch):
    calls = {"n": 0}

    def bad_req(key, model, text):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            "https://example.com", 400, "Bad Request", {}, io.BytesIO(b"unsupported param")
        )

    monkeypatch.setattr(collect, "_post", bad_req)
    res = collect.call_model("k", "m", "oi")
    assert calls["n"] == 1
    assert res["outcome"] == Outcome.REJECTED
    assert res["http_status"] == 400


def test_summarize_run_file_and_write_manifest(tmp_path):
    run_file = tmp_path / "2026-11-13.jsonl"
    r1 = record(model_id="vendor/model-a", cost_usd=0.0015)
    r2 = record(draw=2, model_id="vendor/model-a", cost_usd=0.0025)
    content = json.dumps(r1) + "\n" + json.dumps(r2) + "\n"
    run_file.write_text(content, encoding="utf-8")

    size, sha, cost = collect._summarize_run_file(run_file)
    assert size == len(content.encode("utf-8"))
    assert sha == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert cost == 0.004

    manifest_path = tmp_path / "manifest.json"
    manifest = collect.write_manifest(
        manifest_path,
        run_id="2026-11-13",
        questions=[QUESTION],
        models=["vendor/model-a"],
        count=2,
        total=2,
        target=5,
        elapsed=12.0,
        bytes_written=size,
        file_sha256=sha,
        cost_usd=cost,
        outcomes={"answered": 2},
        canaries_included=True,
    )
    assert manifest["cost_usd"] == 0.004
    assert manifest["file_sha256"] == sha
