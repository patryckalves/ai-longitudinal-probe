"""Data contracts for the probe artifact, enforced at runtime.

The collector writes; this module defines what a valid record is. Every rule here
is checkable without network access: a record that fails validation is a
collector bug, not a model failure.

Language rule: keys, enums and identifiers in English; probe content (question
text, slug, source, model answer) in Portuguese.
"""
from __future__ import annotations

import json
import pathlib
import re
from enum import StrEnum
from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CANARY_PREFIX = "canario-"


class Block(StrEnum):
    """Section of the battery.

    Attributes:
        FACTUAL: Verifiable public facts (F).
        GENERIC: Open explanatory questions (G).
        CALIBRATION: Forecasts with a stated confidence (C).
        CANARY: Negative controls for the plumbing (K).
    """

    FACTUAL = "F"
    GENERIC = "G"
    CALIBRATION = "C"
    CANARY = "K"


class Outcome(StrEnum):
    """How a call ended.

    Attributes:
        ANSWERED: Content came back and was not cut.
        TRUNCATED: Hit our own ``max_tokens`` cap. Not a model failure.
        UNAVAILABLE: Rate limited or server error, after retries.
        REJECTED: The surface refused the call (client error).
        NOT_EVALUATED: Empty content or an unexpected failure.
    """

    ANSWERED = "answered"
    TRUNCATED = "truncated"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    NOT_EVALUATED = "not_evaluated"


class Tokens(BaseModel):
    """Token accounting for one call.

    Attributes:
        tokens_in: Prompt tokens, serialised as ``in``.
        tokens_out: Completion tokens, serialised as ``out``.
        reasoning: Thinking tokens, when the provider reports them.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    tokens_in: Optional[int] = Field(default=None, alias="in")
    tokens_out: Optional[int] = Field(default=None, alias="out")
    reasoning: Optional[int] = None


class Params(BaseModel):
    """Generation parameters actually sent.

    Attributes:
        temperature: Sampling temperature, or ``None`` when omitted to use the provider default.
        max_tokens: Completion cap, reasoning included.
        draw: Repetition index.
    """

    model_config = ConfigDict(extra="forbid")

    temperature: Optional[float] = None
    max_tokens: int
    draw: int


class Record(BaseModel):
    """One call, as persisted. The JSONL is append-only.

    Attributes:
        id: Record identifier.
        run_id: The day of the run, ``YYYY-MM-DD``.
        day: Copy of ``run_id``, for aggregation convenience.
        collected_at: ISO-8601 UTC timestamp.
        question_slug: Stable battery key.
        question_text: The exact text sent.
        instrument_version: Battery version at the time.
        block: Battery section.
        draw: Repetition index, from 1.
        model_id: Model id as sent to the endpoint.
        canonical_slug: Model id without variant or price tag.
        declared_version: Model the provider says it served.
        provider: Provider that served the request, when reported.
        generation_id: Provider response or generation identifier, when reported.
        system_fingerprint: Provider backend/configuration fingerprint, when reported.
        route: Host of the endpoint used.
        params: Generation parameters.
        system_prompt: Always ``None``: we send none.
        answer_text: The answer, verbatim.
        answer_chars: Length of ``answer_text``.
        answer_sha256: Short digest, to spot byte-identical answers.
        outcome: How the call ended.
        error_type: Detail when the outcome is not ``answered``.
        http_status: HTTP status, when there was one.
        finish_reason: Provider finish reason.
        latency_ms: Round trip, which belongs to the route, not the model.
        tokens: Token accounting.
        cost_usd: Nominal cost; zero on free routes.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    day: str
    collected_at: str
    question_slug: str
    question_text: str
    instrument_version: str
    block: Block
    draw: int = Field(ge=1)
    model_id: str
    canonical_slug: str
    declared_version: Optional[str] = None
    provider: Optional[str] = None
    generation_id: Optional[str] = None
    system_fingerprint: Optional[str] = None
    route: str
    params: Params
    system_prompt: Optional[str] = None
    answer_text: str
    answer_chars: int = Field(ge=0)
    answer_sha256: str
    outcome: Outcome
    error_type: Optional[str] = None
    http_status: Optional[int] = None
    finish_reason: Optional[str] = None
    latency_ms: int = Field(ge=0)
    tokens: Tokens
    cost_usd: float = Field(ge=0.0)

    @field_validator("run_id", "day")
    @classmethod
    def _valid_date(cls, value: str) -> str:
        """Check that a date field is ``YYYY-MM-DD``.

        Args:
            value: The field value.

        Returns:
            The unchanged value.

        Raises:
            ValueError: If the format is wrong.
        """
        if not DATE_RE.match(value):
            raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
        return value

    @field_validator("instrument_version")
    @classmethod
    def _valid_version(cls, value: str) -> str:
        """Check that a version field is ``vN.N.N``.

        Args:
            value: The field value.

        Returns:
            The unchanged value.

        Raises:
            ValueError: If the format is wrong.
        """
        if not VERSION_RE.match(value):
            raise ValueError(f"version must be vN.N.N, got {value!r}")
        return value

    @field_validator("question_slug")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        """Check that a slug is lowercase kebab-case.

        Args:
            value: The field value.

        Returns:
            The unchanged value.

        Raises:
            ValueError: If the format is wrong.
        """
        if not SLUG_RE.match(value):
            raise ValueError(f"invalid slug: {value!r}")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> Record:
        """Check the cross-field rules of a record.

        Returns:
            The validated record.

        Raises:
            ValueError: If any pair of fields contradicts itself.
        """
        if self.day != self.run_id:
            raise ValueError(f"day ({self.day}) differs from run_id ({self.run_id})")

        if self.finish_reason == "length" and self.outcome != Outcome.TRUNCATED:
            raise ValueError("finish_reason=length requires outcome=truncated")

        if self.outcome == Outcome.ANSWERED:
            if not self.answer_text.strip():
                raise ValueError("outcome=answered with empty answer_text")
            if self.http_status != 200:
                raise ValueError("outcome=answered requires http_status=200")
        elif self.outcome in (Outcome.UNAVAILABLE, Outcome.REJECTED, Outcome.NOT_EVALUATED):
            if self.answer_text.strip():
                raise ValueError(f"outcome={self.outcome.value} cannot carry an answer")

        if self.answer_chars != len(self.answer_text):
            raise ValueError("answer_chars does not match the text")

        if self.model_id.endswith(":free") and self.cost_usd > 0:
            raise ValueError("model tagged ':free' with cost > 0")

        is_canary = self.question_slug.startswith(CANARY_PREFIX)
        if self.block == Block.CANARY and not is_canary:
            raise ValueError(f"block K requires a slug starting with {CANARY_PREFIX!r}")
        if self.block != Block.CANARY and is_canary:
            raise ValueError(f"slug {CANARY_PREFIX!r} requires block K")

        return self


class Manifest(BaseModel):
    """Per-cycle audit of what the job cost and produced.

    Attributes:
        run_id: The day of the run.
        instrument_version: Battery version used.
        generated_at: ISO-8601 UTC timestamp.
        roster: Models used.
        draws: Draws per cell.
        questions: Battery items used.
        canaries_included: Whether block K took part.
        records_this_run: Records written by this execution.
        records_today: Records in the run file afterwards.
        target_today: Expected records for a complete run.
        coverage: ``records_today / target_today``.
        outcomes_this_run: Count of records per outcome.
        calls: Calls made.
        duration_s: Wall-clock duration.
        actions_minutes: Duration in minutes, for Actions accounting.
        bytes_written: Size of the run file.
        file_sha256: SHA-256 digest of the run file, for integrity verification.
        cost_usd: Nominal cost.
        file: Run file name.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    instrument_version: str
    generated_at: str
    roster: List[str] = Field(min_length=1)
    draws: int = Field(ge=1)
    questions: int = Field(ge=1)
    canaries_included: bool
    records_this_run: int = Field(ge=0)
    records_today: int = Field(ge=0)
    target_today: int = Field(ge=1)
    coverage: float = Field(ge=0.0)
    outcomes_this_run: Dict[str, int]
    calls: int = Field(ge=0)
    duration_s: int = Field(ge=0)
    actions_minutes: float = Field(ge=0.0)
    bytes_written: int = Field(ge=0)
    file_sha256: Optional[str] = None
    cost_usd: float = Field(ge=0.0)
    file: str

    @model_validator(mode="after")
    def _coherent(self) -> Manifest:
        """Check the manifest counters against each other.

        Returns:
            The validated manifest.

        Raises:
            ValueError: If coverage exceeds 1 or records exceed the target.
        """
        if self.coverage > 1.0:
            raise ValueError("coverage > 1")
        if self.records_today > self.target_today:
            raise ValueError("records_today greater than target_today")
        return self


class Canary(BaseModel):
    """Expected content of a negative-control item.

    Attributes:
        expected: Comparison to use. Only ``contains`` is supported.
        value: Text the answer must contain.
    """

    model_config = ConfigDict(extra="forbid")

    expected: Literal["contains"]
    value: str


class Question(BaseModel):
    """One battery item. Text is Portuguese by design; every key is English.

    Attributes:
        slug: Stable identity. Renaming starts a new series.
        block: Battery section.
        version: Battery version this item belongs to.
        active_since: First day in the series.
        expires_at: Last day in the series, for year-bound facts.
        text: The question, in Portuguese, sent verbatim.
        answer_key: Descriptive ground truth, for whoever analyses it.
        source: Where the ground truth comes from.
        tags: Free-form taxonomy.
        canary: Expected content, only in block K.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    block: Block
    version: str
    active_since: str
    expires_at: Optional[str] = None
    text: str
    answer_key: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    canary: Optional[Canary] = None

    @field_validator("version")
    @classmethod
    def _valid_version(cls, value: str) -> str:
        """Check that a version field is ``vN.N.N``.

        Args:
            value: The field value.

        Returns:
            The unchanged value.

        Raises:
            ValueError: If the format is wrong.
        """
        if not VERSION_RE.match(value):
            raise ValueError(f"version must be vN.N.N, got {value!r}")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> Question:
        """Check the cross-field rules of a battery item.

        Returns:
            The validated item.

        Raises:
            ValueError: If the text is empty, the window is inverted, or the
                canary object and the block disagree.
        """
        if not self.text.strip():
            raise ValueError("question without text")
        if self.expires_at and self.expires_at <= self.active_since:
            raise ValueError("expires_at must be after active_since")
        if self.block == Block.CANARY:
            if self.canary is None:
                raise ValueError("block K requires the 'canary' object")
            if not self.slug.startswith(CANARY_PREFIX):
                raise ValueError(f"block K requires a {CANARY_PREFIX!r} slug")
        elif self.canary is not None:
            raise ValueError("'canary' only makes sense in block K")
        return self


def load_questions(path: pathlib.Path) -> List[Question]:
    """Read and validate the battery.

    Args:
        path: JSONL file with one question per line.

    Returns:
        The validated items, in file order.

    Raises:
        SystemExit: If any line is invalid, a slug repeats, or the file is empty.
            The battery is a contract: an inconsistent one must stop the run.
    """
    items: List[Question] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(Question(**json.loads(line)))
        except ValidationError as exc:
            raise SystemExit(f"{path.name}:{lineno}: {exc}") from exc
    slugs = [item.slug for item in items]
    duplicates = {slug for slug in slugs if slugs.count(slug) > 1}
    if duplicates:
        raise SystemExit(f"duplicate slugs in {path.name}: {sorted(duplicates)}")
    if not items:
        raise SystemExit(f"{path.name} is empty")
    versions = {item.version for item in items}
    if len(versions) > 1:
        raise SystemExit(f"mixed instrument versions in {path.name}: {sorted(versions)}")
    return items


def validate_line(line: str) -> Record:
    """Validate a single serialised record.

    Args:
        line: One JSONL line.

    Returns:
        The validated record.

    Raises:
        pydantic.ValidationError: If the line violates the contract.
    """
    return Record(**json.loads(line))
