#!/usr/bin/env python3
"""Collector for the AI longitudinal probe.

Asks a fixed battery of questions to a fixed roster of models, ``PROBE_DRAWS``
times per cell, and persists one JSONL record per call plus a per-cycle
manifest. Any endpoint implementing the OpenAI chat-completions API works.

Environment:
    PROBE_API_KEY: Bearer token for the endpoint. Read from the environment first,
        falling back to a macOS Keychain generic password. Required.
    PROBE_KEYCHAIN_SERVICE: Keychain service name to read. Defaults to
        ``PROBE_API_KEY``.
    PROBE_BASE_URL: Base URL without ``/chat/completions``. Defaults to
        ``https://openrouter.ai/api/v1``.
    PROBE_DRAWS: Accepted answers per cell. Defaults to ``5``.
    PROBE_MODELS: Comma-separated model ids. Defaults to ``ROSTER``.
    PROBE_RUN_DATE: Run date as ``YYYY-MM-DD``. Defaults to today in UTC-3.
    PROBE_OUT: Output JSONL path. Defaults to ``data/<run_id>.jsonl``.
    PROBE_MANIFEST_DIR: Manifest directory. Defaults to ``manifests``.
    PROBE_LIMIT_CALLS: Hard ceiling of calls per run. Defaults to ``400``.
    PROBE_SKIP_CANARIES: ``1`` to leave block K out of the run.

Notes:
    Reasoning models bill thinking tokens into ``max_tokens``. With a low cap the
    content comes back empty while the token usage is full, which reads as a
    false ``not_evaluated``. ``MAX_TOKENS`` is sized against that behaviour, not
    against answer length.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from schema import Manifest, Outcome, Question, Record, load_questions

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "questions.jsonl"
DEFAULT_KEYCHAIN_SERVICE = "PROBE_API_KEY"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

BASE_URL = (os.environ.get("PROBE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
ENDPOINT = f"{BASE_URL}/chat/completions"
ROUTE = urllib.parse.urlparse(BASE_URL).netloc or BASE_URL

DRAWS = int(os.environ.get("PROBE_DRAWS", "5"))
MAX_TOKENS = 1500
PACING_S = 3.2
HARD_TIMEOUT_S = 120
RETRIES = 2
RETRY_BACKOFF_S = (20, 40)

ROSTER = [
    "baidu/ernie-5.5-flash-preview:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "cohere/north-mini-code:free",
    "dots-studio/dots-3-note-preview:free",
]

Cell = Tuple[str, str, str, int]


class _Timeout(Exception):
    """Raised by the wall-clock alarm when a call overruns ``HARD_TIMEOUT_S``."""


def _alarm(signum, frame) -> None:
    """Signal handler that turns the alarm into an exception."""
    raise _Timeout()


def today_brt() -> str:
    """Return today's date in UTC-3 as ``YYYY-MM-DD``.

    Returns:
        The current date in Brasília time.
    """
    return (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%d")


def _keychain_lookup(service: str) -> Optional[str]:
    """Read a generic password from the macOS Keychain.

    Args:
        service: Keychain service name.

    Returns:
        The stored password, or ``None`` when there is no such entry, the item
        has no password, or the platform has no ``security`` command.
    """
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    value = result.stdout.strip()
    return value or None


def load_api_key(service: Optional[str] = None) -> str:
    """Return the API key, preferring the environment over the macOS Keychain.

    Args:
        service: Keychain service name. Defaults to ``PROBE_KEYCHAIN_SERVICE``,
            then to ``PROBE_API_KEY``.

    Returns:
        The key, stripped of quotes and whitespace.

    Raises:
        RuntimeError: If neither the environment nor the Keychain has it. This is
            a configuration error, so ``main`` turns it into exit code 2.
    """
    value = os.environ.get("PROBE_API_KEY")
    if value:
        return value.strip().strip('"').strip("'")

    name = (
        service
        or os.environ.get("PROBE_KEYCHAIN_SERVICE")
        or DEFAULT_KEYCHAIN_SERVICE
    )
    stored = _keychain_lookup(name)
    if stored:
        return stored

    raise RuntimeError(
        f"PROBE_API_KEY not found in the environment or in the Keychain "
        f"(service {name!r})"
    )


def classify_outcome(finish_reason: Optional[str], text: str) -> Outcome:
    """Map a raw API response to one of the five outcome values.

    A ``length`` finish is truncation caused by our own token cap, so it is not
    treated as a model failure.

    Args:
        finish_reason: The provider's ``finish_reason``, if any.
        text: The response content, already stripped.

    Returns:
        ``TRUNCATED``, ``ANSWERED`` or ``NOT_EVALUATED``.
    """
    if finish_reason == "length":
        return Outcome.TRUNCATED
    if (text or "").strip():
        return Outcome.ANSWERED
    return Outcome.NOT_EVALUATED


def already_collected(path: pathlib.Path) -> Set[Cell]:
    """List the cells already present in a run file, for resuming.

    Args:
        path: JSONL file of the current run.

    Returns:
        A set of ``(run_id, question_slug, model_id, draw)`` tuples. Malformed
        lines are skipped rather than raising, so a crashed run stays resumable.
    """
    done: Set[Cell] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        done.add(
            (
                record["run_id"],
                record["question_slug"],
                record["model_id"],
                record["draw"],
            )
        )
    return done


def _post(key: str, model: str, text: str) -> dict:
    """Send one chat-completion request and decode the JSON body.

    Args:
        key: Bearer token.
        model: Model id as the endpoint expects it.
        text: The question.

    Returns:
        The decoded response body.

    Raises:
        urllib.error.HTTPError: On a non-2xx response.
        _Timeout: If the call exceeds ``HARD_TIMEOUT_S`` wall-clock seconds.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": MAX_TOKENS,
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=HARD_TIMEOUT_S) as response:
        return json.loads(response.read())


def _empty_result(
    outcome: Outcome,
    error_type: Optional[str],
    http_status: Optional[int],
    latency_ms: int,
) -> Dict[str, object]:
    """Build the result payload for a call that produced no answer.

    Args:
        outcome: One of the failure outcomes.
        error_type: Short machine-readable description of what happened.
        http_status: HTTP status code when the failure came from a response.
        latency_ms: Elapsed time of the attempt.

    Returns:
        A dict shaped like a successful result, with empty content.
    """
    return {
        "answer_text": "",
        "outcome": outcome,
        "error_type": error_type,
        "http_status": http_status,
        "latency_ms": latency_ms,
        "tokens": {"in": None, "out": None, "reasoning": None},
        "cost_usd": 0.0,
        "declared_version": None,
        "provider": None,
        "generation_id": None,
        "system_fingerprint": None,
        "finish_reason": None,
    }


def call_model(key: str, model: str, text: str) -> dict:
    """Call one model, retrying transient failures and classifying the ending.

    Args:
        key: Bearer token.
        model: Model id as the endpoint expects it.
        text: The question.

    Returns:
        A dict with the answer, outcome, tokens, cost and provenance fields.
        Every failure mode is mapped to an outcome; nothing is raised.
    """
    started = time.time()
    failure: Optional[Tuple[Outcome, str, Optional[int]]] = None

    for attempt in range(RETRIES + 1):
        signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, HARD_TIMEOUT_S)
        try:
            body = _post(key, model, text)
            choice = (body.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            answer = (message.get("content") or "").strip()
            finish = choice.get("finish_reason")
            usage = body.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            return {
                "answer_text": answer,
                "outcome": classify_outcome(finish, answer),
                "error_type": None if answer else "empty_response",
                "http_status": 200,
                "latency_ms": int((time.time() - started) * 1000),
                "tokens": {
                    "in": usage.get("prompt_tokens"),
                    "out": usage.get("completion_tokens"),
                    "reasoning": details.get("reasoning_tokens"),
                },
                "cost_usd": float(usage.get("cost") or 0.0),
                "declared_version": body.get("model"),
                "provider": body.get("provider"),
                "generation_id": body.get("id"),
                "system_fingerprint": body.get("system_fingerprint"),
                "finish_reason": finish,
            }
        except _Timeout:
            return _empty_result(
                Outcome.NOT_EVALUATED,
                f"hard_timeout_{HARD_TIMEOUT_S}s",
                None,
                int((time.time() - started) * 1000),
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 429 or exc.code >= 500:
                failure = (Outcome.UNAVAILABLE, f"http_{exc.code}: {detail}", exc.code)
                if attempt < RETRIES:
                    time.sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
                    continue
                break
            failure = (Outcome.REJECTED, f"http_{exc.code}: {detail}", exc.code)
            break
        except Exception as exc:
            failure = (Outcome.NOT_EVALUATED, f"{type(exc).__name__}: {str(exc)[:200]}", None)
            break
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    if failure is None:
        failure = (Outcome.NOT_EVALUATED, "unexpected_exit", None)
    return _empty_result(*failure, int((time.time() - started) * 1000))


def build_record(
    *, run_id: str, question: Question, model: str, draw: int, result: dict
) -> dict:
    """Assemble one record and validate it against the contract.

    Args:
        run_id: The day of the run, as ``YYYY-MM-DD``.
        question: The battery item that was asked.
        model: Model id as sent to the endpoint.
        draw: Repetition index, starting at 1.
        result: Payload returned by :func:`call_model`.

    Returns:
        The record serialised with the on-disk field names.

    Raises:
        pydantic.ValidationError: If the record violates the contract. This is a
            collector bug, so it stops the run instead of being written.
    """
    record = {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "day": run_id,
        "collected_at": datetime.now(UTC).isoformat(),
        "question_slug": question.slug,
        "question_text": question.text,
        "instrument_version": question.version,
        "block": question.block.value,
        "draw": draw,
        "model_id": model,
        "canonical_slug": model.split("/")[-1].replace(":free", ""),
        "declared_version": result["declared_version"],
        "provider": result["provider"],
        "generation_id": result.get("generation_id"),
        "system_fingerprint": result.get("system_fingerprint"),
        "route": ROUTE,
        "params": {"temperature": None, "max_tokens": MAX_TOKENS, "draw": draw},
        "system_prompt": None,
        "answer_text": result["answer_text"],
        "answer_chars": len(result["answer_text"]),
        "answer_sha256": hashlib.sha256(result["answer_text"].encode()).hexdigest()[:16],
        "outcome": result["outcome"].value,
        "error_type": result["error_type"],
        "http_status": result["http_status"],
        "finish_reason": result["finish_reason"],
        "latency_ms": result["latency_ms"],
        "tokens": result["tokens"],
        "cost_usd": result["cost_usd"],
    }
    return Record(**record).model_dump(mode="json", by_alias=True)


def _summarize_run_file(path: pathlib.Path) -> Tuple[int, Optional[str], float]:
    """Compute size, SHA-256 digest and total cost of the run file.

    Args:
        path: JSONL file of the current run.

    Returns:
        ``(bytes_written, file_sha256, cost_usd)``.
    """
    if not path.exists():
        return 0, None, 0.0
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    total_cost = 0.0
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            total_cost += float(json.loads(line).get("cost_usd") or 0.0)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return len(raw), digest, round(total_cost, 6)


def write_manifest(
    path: pathlib.Path,
    *,
    run_id: str,
    questions: List[Question],
    models: List[str],
    count: int,
    total: int,
    target: int,
    elapsed: float,
    bytes_written: int,
    file_sha256: Optional[str],
    cost_usd: float,
    outcomes: Dict[str, int],
    canaries_included: bool,
) -> dict:
    """Build, validate and write the cycle manifest.

    Args:
        path: Destination file.
        run_id: The day of the run.
        questions: Battery items used in this run.
        models: Roster used in this run.
        count: Records written by this execution.
        total: Records present in the run file afterwards.
        target: Expected records for a complete run.
        elapsed: Duration in seconds.
        bytes_written: Size of the run file.
        file_sha256: SHA-256 digest of the run file.
        cost_usd: Total nominal cost of the run file.
        outcomes: Count of records per outcome.
        canaries_included: Whether block K took part.

    Returns:
        The manifest as written.

    Raises:
        pydantic.ValidationError: If the manifest violates its contract.
    """
    manifest = {
        "run_id": run_id,
        "instrument_version": questions[0].version,
        "generated_at": datetime.now(UTC).isoformat(),
        "roster": models,
        "draws": DRAWS,
        "questions": len(questions),
        "canaries_included": canaries_included,
        "records_this_run": count,
        "records_today": total,
        "target_today": target,
        "coverage": round(total / target, 3),
        "outcomes_this_run": outcomes,
        "calls": count,
        "duration_s": int(elapsed),
        "actions_minutes": round(elapsed / 60, 1),
        "bytes_written": bytes_written,
        "file_sha256": file_sha256,
        "cost_usd": cost_usd,
        "file": path.name,
    }
    Manifest(**manifest)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> int:
    """Run one full collection cycle.

    Returns:
        Process exit code: 0 on success, 2 when configuration is missing.
    """
    try:
        key = load_api_key()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run_id = os.environ.get("PROBE_RUN_DATE") or today_brt()
    out = pathlib.Path(os.environ.get("PROBE_OUT") or ROOT / "data" / f"{run_id}.jsonl")
    manifest_dir = pathlib.Path(
        os.environ.get("PROBE_MANIFEST_DIR") or ROOT / "manifests"
    )
    limit = int(os.environ.get("PROBE_LIMIT_CALLS", "400"))
    models = [
        model.strip()
        for model in (os.environ.get("PROBE_MODELS") or ",".join(ROSTER)).split(",")
        if model.strip()
    ]
    skip_canaries = os.environ.get("PROBE_SKIP_CANARIES") == "1"

    if not models:
        print("error: PROBE_MODELS is empty", file=sys.stderr)
        return 2

    questions = load_questions(QUESTIONS)
    if skip_canaries:
        questions = [question for question in questions if question.block.value != "K"]
    if not questions:
        print("error: battery is empty", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    done = already_collected(out)
    target = len(models) * len(questions) * DRAWS
    print(
        f"run_id={run_id}  models={len(models)}  questions={len(questions)}  "
        f"draws={DRAWS}  target={target}  already={len(done)}",
        flush=True,
    )

    started = time.time()
    count = 0
    stop = False
    outcomes: Dict[str, int] = {}

    with out.open("a", encoding="utf-8") as handle:
        for draw in range(1, DRAWS + 1):
            if stop:
                break
            for question in questions:
                if stop:
                    break
                for model in models:
                    if (run_id, question.slug, model, draw) in done:
                        continue
                    if len(done) + count >= limit:
                        print("call ceiling reached (PROBE_LIMIT_CALLS)", flush=True)
                        stop = True
                        break
                    result = call_model(key, model, question.text)
                    record = build_record(
                        run_id=run_id,
                        question=question,
                        model=model,
                        draw=draw,
                        result=result,
                    )
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    count += 1
                    outcomes[record["outcome"]] = outcomes.get(record["outcome"], 0) + 1
                    flag = ""
                    if record["outcome"] != Outcome.ANSWERED.value:
                        flag = f"  !! {record['outcome']} {str(record['error_type'])[:80]}"
                    print(
                        f"[{count:>3}] {model:<44} {question.slug:<24} d{draw} "
                        f"{record['latency_ms']:>6}ms "
                        f"{record['tokens'].get('out') or 0:>4}tok{flag}",
                        flush=True,
                    )
                    time.sleep(PACING_S)

    elapsed = time.time() - started
    total = len(done) + count
    bytes_written, file_sha256, total_cost = _summarize_run_file(out)
    manifest_path = manifest_dir / f"{run_id}.json"
    write_manifest(
        manifest_path,
        run_id=run_id,
        questions=questions,
        models=models,
        count=count,
        total=total,
        target=target,
        elapsed=elapsed,
        bytes_written=bytes_written,
        file_sha256=file_sha256,
        cost_usd=total_cost,
        outcomes=outcomes,
        canaries_included=not skip_canaries,
    )
    print(
        f"\n{count} new · {total}/{target} today · {elapsed/60:.1f} min · "
        f"{bytes_written} bytes · {outcomes}",
        flush=True,
    )
    print(f"manifest -> {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
