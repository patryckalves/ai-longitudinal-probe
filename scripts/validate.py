#!/usr/bin/env python3
"""Runtime validation of the day's artifact.

Checks the artifact only, never the content of the answers, in three families:
structure (the record contract), data quality (duplicates, single day,
instrument version, matrix gaps, provider caching) and the canary smoke test.

Exit code 1 means a hard failure, so the workflow goes red instead of publishing
a corrupt artifact that looks like data.

Environment:
    PROBE_QUESTIONS: Battery file. Defaults to ``<repo>/questions.jsonl``.
    PROBE_MANIFEST_DIR: Manifest directory. Defaults to ``manifests``.
    PROBE_DATA_DIR: Directory searched when ``--file`` is omitted. Defaults to
        ``data``.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

from pydantic import ValidationError
from schema import Question, Record, load_questions

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUESTIONS = pathlib.Path(os.environ.get("PROBE_QUESTIONS") or ROOT / "questions.jsonl")
MANIFEST_DIR = pathlib.Path(os.environ.get("PROBE_MANIFEST_DIR") or ROOT / "manifests")
DATA_DIR = pathlib.Path(os.environ.get("PROBE_DATA_DIR") or ROOT / "data")


def today_brt() -> str:
    """Return today's date in UTC-3 as ``YYYY-MM-DD``.

    Returns:
        The current date in Brasília time.
    """
    return (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%d")


class Report:
    """Collects check results and renders them for both humans and GitHub."""

    def __init__(self) -> None:
        """Start an empty report."""
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passed: List[str] = []

    def error(self, message: str) -> None:
        """Record a hard failure.

        Args:
            message: What failed.
        """
        self.errors.append(message)

    def warn(self, message: str) -> None:
        """Record a non-blocking finding.

        Args:
            message: What was noticed.
        """
        self.warnings.append(message)

    def ok(self, message: str) -> None:
        """Record a passed check.

        Args:
            message: What passed.
        """
        self.passed.append(message)

    def dump(self) -> None:
        """Print the report as ``::error::`` and ``::warning::`` annotations."""
        for message in self.passed:
            print(f"  ok    {message}")
        for message in self.warnings:
            print(f"::warning::{message}")
        for message in self.errors:
            print(f"::error::{message}")
        print(
            f"\n{len(self.passed)} ok · {len(self.warnings)} warning(s) · "
            f"{len(self.errors)} error(s)"
        )


def load_records(path: pathlib.Path, report: Report) -> List[Record]:
    """Read and validate a run file, line by line.

    Args:
        path: JSONL file to read.
        report: Report that receives one error per invalid line.

    Returns:
        The records that validated. Invalid lines are reported, not raised, so a
        single bad line still lets the rest be checked.
    """
    records: List[Record] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(Record(**json.loads(line)))
        except json.JSONDecodeError as exc:
            report.error(f"{path.name}:{lineno} invalid JSON: {exc}")
        except ValidationError as exc:
            fields = ", ".join(
                f"{'.'.join(str(x) for x in detail['loc'])}: {detail['msg']}"
                for detail in exc.errors()
            )
            report.error(f"{path.name}:{lineno} invalid record — {fields}")
    if records:
        report.ok(f"structure: {len(records)} record(s) validated against the schema")
    return records


def _check_day(records: List[Record], report: Report) -> Optional[str]:
    """Check that the file holds exactly one day.

    Args:
        records: Validated records.
        report: Report that receives the finding.

    Returns:
        The single day, or ``None`` when the file mixes days.
    """
    days = {record.run_id for record in records}
    if len(days) > 1:
        report.error(f"file mixes {len(days)} days: {sorted(days)} — one file per day")
        return None
    day = sorted(days)[0]
    report.ok(f"single day: {day}")
    return day


def _check_duplicates(records: List[Record], report: Report) -> None:
    """Check that no cell appears twice.

    Args:
        records: Validated records.
        report: Report that receives the finding.
    """
    keys = Counter(
        (record.run_id, record.question_slug, record.model_id, record.draw)
        for record in records
    )
    duplicates = [key for key, count in keys.items() if count > 1]
    if duplicates:
        report.error(f"{len(duplicates)} duplicate cell(s), e.g. {duplicates[0]}")
    else:
        report.ok(f"no duplicates across {len(keys)} cells")


def _check_instrument_version(
    records: List[Record], questions: List[Question], day: Optional[str], report: Report
) -> None:
    """Check that one instrument version is used and that it is the current one.

    Args:
        records: Validated records.
        questions: Current battery.
        day: The single day in the file, if any.
        report: Report that receives the finding.
    """
    versions = {record.instrument_version for record in records}
    if len(versions) > 1:
        report.error(f"file mixes instrument versions: {sorted(versions)}")
        return
    if not questions:
        return
    current = questions[0].version
    if versions == {current}:
        return
    message = (
        f"instrument_version in file ({sorted(versions)[0]}) "
        f"differs from current battery ({current})"
    )
    if day is None or day == today_brt():
        report.error(message + " — today's collection used the wrong battery")
    else:
        report.warn(message + " — expected in a historical file")


def _check_outcomes(records: List[Record], report: Report) -> None:
    """Check that the run produced at least one answer.

    Args:
        records: Validated records.
        report: Report that receives the finding.
    """
    outcomes = Counter(record.outcome.value for record in records)
    if outcomes.get("answered", 0) == 0:
        report.error("no answers collected — instrument broken")
    else:
        report.ok(f"outcomes: {dict(outcomes)}")


def _check_matrix_gaps(
    records: List[Record], questions: List[Question], report: Report
) -> None:
    """Check that every model has a record for every question.

    Args:
        records: Validated records.
        questions: Current battery.
        report: Report that receives one warning per incomplete model.
    """
    slugs = {question.slug for question in questions}
    present: Dict[str, set] = defaultdict(set)
    for record in records:
        present[record.model_id].add(record.question_slug)
    for model, seen in sorted(present.items()):
        missing = slugs - seen
        if missing:
            report.warn(
                f"{model}: no record for {len(missing)} question(s) "
                f"(e.g. {sorted(missing)[0]})"
            )


def _check_truncation(records: List[Record], report: Report, max_truncation: float) -> None:
    """Warn about models whose answers routinely hit the token cap.

    Args:
        records: Validated records.
        report: Report that receives one warning per affected model.
        max_truncation: Share of truncated answers above which a warning is raised.
    """
    by_model: Dict[str, List[Record]] = defaultdict(list)
    for record in records:
        by_model[record.model_id].append(record)
    for model, rows in sorted(by_model.items()):
        truncated = sum(1 for row in rows if row.outcome.value == "truncated")
        rate = truncated / len(rows)
        if rate > max_truncation:
            report.warn(
                f"{model}: {truncated}/{len(rows)} truncated ({rate:.0%}) — "
                f"token cap or long-reasoning model"
            )


def _check_circuit_breaker(records: List[Record], report: Report) -> None:
    """Warn about models that tripped the availability circuit breaker.

    Args:
        records: Validated records.
        report: Report that receives one warning per affected model.
    """
    by_model: Dict[str, List[Record]] = defaultdict(list)
    for record in records:
        by_model[record.model_id].append(record)
    for model, rows in sorted(by_model.items()):
        skipped = sum(1 for row in rows if row.error_type == "circuit_breaker_open")
        if skipped:
            report.warn(
                f"{model}: circuit breaker opened ({skipped}/{len(rows)} cell(s) "
                f"marked unavailable)"
            )


def _check_cache(records: List[Record], report: Report) -> None:
    """Warn when every draw of a cell returned a byte-identical answer.

    Args:
        records: Validated records.
        report: Report that receives the finding.
    """
    by_cell: Dict[tuple, List[str]] = defaultdict(list)
    for record in records:
        if record.outcome.value in ("answered", "truncated") and record.answer_sha256:
            by_cell[(record.question_slug, record.model_id)].append(record.answer_sha256)
    cached = [
        key for key, digests in by_cell.items() if len(digests) > 1 and len(set(digests)) == 1
    ]
    if cached:
        report.warn(
            f"{len(cached)} cell(s) with identical answers across all draws "
            f"(possible provider cache), e.g. {cached[0]}"
        )


def _check_canaries(
    records: List[Record], questions: List[Question], report: Report
) -> None:
    """Run the canary smoke test per model.

    Args:
        records: Validated records.
        questions: Current battery, read for the canary block.
        report: Report that receives the finding. Hitting fewer than half of the
            canaries is a hard failure: it points at the plumbing, not at the model.
    """
    canaries = {question.slug: question for question in questions if question.canary}
    if not canaries:
        report.warn("battery has no canary — the day's plumbing was NOT validated")
        return

    models = sorted({record.model_id for record in records})
    for model in models:
        evaluated = 0
        hits = 0
        for record in records:
            if record.model_id != model or record.question_slug not in canaries:
                continue
            if record.outcome.value not in ("answered", "truncated"):
                continue
            expected = canaries[record.question_slug].canary.value.lower()
            evaluated += 1
            if record.answer_text and expected in record.answer_text.lower():
                hits += 1
        if not evaluated:
            report.warn(f"{model}: no canary collected")
            continue
        rate = hits / evaluated
        if rate < 0.5:
            report.error(
                f"canary: {model} hit {hits}/{evaluated} ({rate:.0%}) — "
                f"suspicious plumbing, do not read behaviour on this day"
            )
        elif rate < 1.0:
            report.warn(f"canary: {model} hit {hits}/{evaluated} ({rate:.0%})")
        else:
            report.ok(f"canary: {model} {hits}/{evaluated}")


def _check_expired_questions(
    questions: List[Question], day: Optional[str], report: Report
) -> None:
    """Warn when the battery contains items expired for the run day.

    Args:
        questions: Current battery.
        day: The single day in the file, if any.
        report: Report that receives the finding.
    """
    if not day:
        return
    expired = [q.slug for q in questions if q.expires_at and day > q.expires_at]
    if expired:
        report.warn(f"{len(expired)} expired question(s) for {day}, e.g. {expired[0]}")


def _check_coverage(
    records: List[Record],
    manifest: Optional[dict],
    report: Report,
    min_coverage: float,
) -> None:
    """Compare the number of records against the manifest target.

    Args:
        records: Validated records.
        manifest: The cycle manifest, when available.
        report: Report that receives the finding.
        min_coverage: Share below which coverage is a hard failure.
    """
    if not manifest:
        return
    target = manifest.get("target_today")
    if not target:
        return
    coverage = len(records) / target
    if coverage < min_coverage:
        report.error(
            f"coverage {coverage:.0%} below minimum {min_coverage:.0%} "
            f"({len(records)}/{target})"
        )
    else:
        report.ok(f"coverage {coverage:.0%} ({len(records)}/{target})")


def run_checks(
    records: List[Record],
    questions: List[Question],
    report: Report,
    manifest: Optional[dict],
    min_coverage: float,
    max_truncation: float,
) -> None:
    """Run every check against one day of records.

    Args:
        records: Validated records.
        questions: Current battery.
        report: Report that receives all findings.
        manifest: The cycle manifest, when available.
        min_coverage: Share below which coverage is a hard failure.
        max_truncation: Share of truncated answers above which a warning is raised.
    """
    if not records:
        report.error("no valid record in the file")
        return
    day = _check_day(records, report)
    _check_duplicates(records, report)
    _check_instrument_version(records, questions, day, report)
    _check_expired_questions(questions, day, report)
    _check_outcomes(records, report)
    _check_matrix_gaps(records, questions, report)
    _check_truncation(records, report, max_truncation)
    _check_circuit_breaker(records, report)
    _check_cache(records, report)
    _check_canaries(records, questions, report)
    _check_coverage(records, manifest, report, min_coverage)


def _resolve_file(explicit: Optional[str]) -> Optional[pathlib.Path]:
    """Pick the file to validate.

    Args:
        explicit: Path given with ``--file``, if any.

    Returns:
        The resolved path, or ``None`` when nothing was found.
    """
    if explicit:
        return pathlib.Path(explicit)
    candidates = sorted(DATA_DIR.glob("*.jsonl"))
    return candidates[-1] if candidates else None


def main() -> int:
    """Validate one day's artifact.

    Returns:
        Process exit code: 0 when every check passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="day jsonl (default: most recent in data/)")
    parser.add_argument("--questions", default=str(QUESTIONS))
    parser.add_argument("--manifest", help="day manifest (default: matched by filename)")
    parser.add_argument("--min-coverage", type=float, default=0.5)
    parser.add_argument("--max-truncation", type=float, default=0.5)
    args = parser.parse_args()

    path = _resolve_file(args.file)
    if path is None:
        print(f"::error::no file in {DATA_DIR}")
        return 1
    if not path.exists():
        print(f"::error::file not found: {path}")
        return 1

    report = Report()
    questions = load_questions(pathlib.Path(args.questions))

    manifest: Optional[dict] = None
    manifest_path = (
        pathlib.Path(args.manifest) if args.manifest else MANIFEST_DIR / f"{path.stem}.json"
    )
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.warn(f"unreadable manifest ({manifest_path.name}): {exc}")

    print(f"validating {path.name}" + (f" + {manifest_path.name}" if manifest else ""))
    records = load_records(path, report)
    run_checks(
        records,
        questions,
        report,
        manifest,
        args.min_coverage,
        args.max_truncation,
    )
    report.dump()
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
