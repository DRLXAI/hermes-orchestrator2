"""A dependency-free Jev client that works whether or not you have early access.

Jev is reachable two ways and they are not interchangeable in the way you would
expect. OpenRouter serves decision models on a dedicated endpoint --
`POST /api/alpha/decisions` -- and explicitly refuses them on `/chat/completions`.
TypeSafe's own API is `POST /v1/systemone`. Same protocol, same response shape,
different URL and different model namespace, so `jev-latest` and
`typesafe/jev-1.13` are not aliases of each other.

This matters for the kit's whole premise. TypeSafe's direct API was still behind
an early-access waitlist at launch, and most people reading this cannot get a
key today. OpenRouter serves the same model to anyone with an account, which is
why calibrating against it is a thing you can do this afternoon rather than
whenever the waitlist clears.

No SDK, no pydantic, no requests: stdlib only. That is deliberate. This code
runs inside other people's repositories, often in a CI job, and a calibration
tool that drags in a dependency tree is a calibration tool nobody installs.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"

OPENROUTER_MODEL = "typesafe/jev-1.13"
TYPESAFE_MODEL = "jev-latest"

#: Statuses where the identical request could plausibly succeed. Any other 4xx
#: means the request is wrong and retrying only spends money.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 520, 521, 522, 524, 529})
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.5


class JevError(RuntimeError):
    """A provider rejected the request or answered with something unusable."""


class MissingKeyError(JevError):
    """Neither provider's credentials were present in the environment."""


def noul(instructions: str) -> dict[str, Any]:
    """A yes/no question. Returns P(yes) as a bare float, with no confidence field.

    Note the polarity trap: TypeSafe documents that jev-1.13 degrades when the
    instructions and the criteria pull in opposite directions -- a Noul phrased
    so that "true" means "no" scores worse than the same question phrased
    positively. Write the condition you want to be true.
    """
    return {"type": "noul", "instructions": instructions}


def choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    """One of a fixed set. Always include an explicit none-of-these option.

    The model cannot return a value you did not offer it, so without an escape
    hatch every out-of-scope input is forced into one of your real categories
    and arrives looking like a confident answer.
    """
    if len(criteria) < 2:
        raise ValueError("a choice needs at least two options")
    if len(criteria) > 255:
        raise ValueError(f"Jev accepts at most 255 choice options, got {len(criteria)}")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> dict[str, Any]:
    """A position along ordered levels. The answer may land between two of them.

    Levels are indexed from zero, so a returned 1.6 sits between the level at
    index 1 and the one at index 2.
    """
    if len(criteria) < 2:
        raise ValueError("a score needs at least two ordered levels")
    return {"type": "score", "instructions": instructions, "criteria": criteria}


@dataclass(frozen=True)
class Answer:
    """One typed answer, normalised across primitives.

    `confidence` is the field to be careful with. Noul does not have one -- for
    a Noul this is the distance of P(yes) from the coin flip, rescaled to
    [0, 1], so that "how sure is the model" means the same thing across all
    three primitives when you go to threshold it. That is a convenience of this
    kit, not something Jev returns.
    """

    question: str
    kind: str
    value: Any
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """A whole response: every answer, plus what actually served it."""

    model: str
    answers: dict[str, Answer]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    def __getitem__(self, question: str) -> Answer:
        return self.answers[question]

    @property
    def cost_usd(self) -> float:
        """At the listed $0.042 per million input tokens; output is free."""
        return self.input_tokens / 1_000_000 * 0.042


def _parse_answer(question: str, payload: dict[str, Any]) -> Answer:
    """Normalise one answer object into an `Answer`.

    Raises:
        JevError: On an answer type this kit does not know. Failing here beats
            silently dropping it: a new primitive that vanishes from a
            calibration set would corrupt the fit rather than announce itself.
    """
    kind = payload.get("type")
    if kind == "noul":
        p = float(payload["noul"])
        return Answer(
            question=question,
            kind="noul",
            value=p,
            # Distance from the coin flip, rescaled. 0.5 -> 0.0, 0/1 -> 1.0.
            confidence=abs(p - 0.5) * 2.0,
            raw=payload,
        )
    if kind == "choice":
        return Answer(
            question=question,
            kind="choice",
            value=payload["choice"],
            confidence=float(payload.get("confidence", 0.0)),
            probabilities={k: float(v) for k, v in payload.get("probabilities", {}).items()},
            raw=payload,
        )
    if kind == "score":
        return Answer(
            question=question,
            kind="score",
            value=float(payload["score"]),
            confidence=float(payload.get("confidence", 0.0)),
            probabilities={k: float(v) for k, v in payload.get("probabilities", {}).items()},
            raw=payload,
        )
    raise JevError(
        f"unknown answer type {kind!r} for question {question!r}. "
        f"This kit was written against jev-1.13; a new primitive needs handling "
        f"before any threshold fitted on it can be trusted."
    )


@dataclass(frozen=True)
class Provider:
    """Which service answers, as which model, at which URL."""

    name: str
    model: str
    url: str
    api_key: str

    @property
    def label(self) -> str:
        """Safe to print: never contains the key."""
        return f"{self.name} · {self.model}"


def resolve_provider(
    *, prefer: str | None = None, env: dict[str, str] | None = None
) -> Provider:
    """Pick a provider from the environment.

    TypeSafe direct wins when both are present, because a team holding an
    early-access key is calibrating against the thing they will ship on.

    Args:
        prefer: Force `"typesafe"` or `"openrouter"`.
        env: Environment to read. Defaults to `os.environ`.

    Returns:
        The selected provider.

    Raises:
        MissingKeyError: When the needed key is absent.
        ValueError: When `prefer` is not a known provider.
    """
    source = os.environ if env is None else env
    typesafe_key = source.get("TYPESAFE_API_KEY", "")
    openrouter_key = source.get("OPENROUTER_API_KEY", "")

    if prefer not in (None, "typesafe", "openrouter"):
        raise ValueError(f"unknown provider {prefer!r}; use 'typesafe' or 'openrouter'")

    if prefer == "typesafe" or (prefer is None and typesafe_key):
        if not typesafe_key:
            raise MissingKeyError("TYPESAFE_API_KEY is not set")
        return Provider(
            name="typesafe",
            model=source.get("TYPESAFE_MODEL", TYPESAFE_MODEL),
            url=TYPESAFE_SYSTEMONE_URL,
            api_key=typesafe_key,
        )
    if not openrouter_key:
        raise MissingKeyError(
            "No credentials found. Set OPENROUTER_API_KEY to reach Jev today "
            "without early access, or TYPESAFE_API_KEY if you have a direct key."
        )
    return Provider(
        name="openrouter",
        model=source.get("JEV_MODEL", OPENROUTER_MODEL),
        url=OPENROUTER_DECISIONS_URL,
        api_key=openrouter_key,
    )


class JevClient:
    """Ask a state a map of typed questions, in one request."""

    def __init__(self, provider: Provider | None = None, timeout: float = 60.0) -> None:
        self.provider = provider or resolve_provider()
        self._timeout = timeout

    def ask(self, state: Any, questions: dict[str, dict[str, Any]]) -> Decision:
        """Answer every question against the state in a single parallel request.

        Asking seven questions costs about what asking one costs in wall-clock
        time, which is the whole reason to decompose a judgment into narrow
        questions rather than one broad one.

        Args:
            state: A string, a JSON-shaped object, or a list of text values.
            questions: Question objects keyed by an id you choose. The ids are
                yours, are echoed back, and are not shown to the model.

        Returns:
            The parsed decision, with latency and token usage attached.

        Raises:
            JevError: On transport failure, a non-2xx response, or a body that
                is not a System One response.
        """
        if not questions:
            raise ValueError("ask() needs at least one question")

        payload = json.dumps(
            {"model": self.provider.model, "state": state, "questions": questions}
        ).encode()
        request = urllib.request.Request(
            self.provider.url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.provider.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/jev-threshold-kit",
                "X-Title": "jev-threshold-kit",
            },
            method="POST",
        )

        started = time.monotonic()
        body = self._send(request)
        latency_ms = (time.monotonic() - started) * 1000.0

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise JevError(f"{self.provider.name} returned non-JSON: {body[:300]}") from error

        answers_payload = parsed.get("answers")
        if not isinstance(answers_payload, dict):
            raise JevError(
                f"{self.provider.name} returned no answers object: {body[:300]}"
            )
        usage = parsed.get("usage") or {}
        return Decision(
            # The resolved, versioned id. Log this: `jev-latest` moves, and a
            # threshold fitted under one version is not valid under the next.
            model=str(parsed.get("model", self.provider.model)),
            answers={
                name: _parse_answer(name, value) for name, value in answers_payload.items()
            },
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            latency_ms=latency_ms,
        )

    def _send(self, request: urllib.request.Request) -> str:
        """POST with bounded retries on the statuses worth retrying."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return response.read().decode()
            except urllib.error.HTTPError as error:
                if error.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    detail = error.read().decode()[:400]
                    raise JevError(
                        f"{self.provider.name} returned {error.code}: {detail}"
                    ) from error
            except OSError as error:
                # Broader than URLError on purpose: urllib only wraps the connect
                # phase, so a read timeout surfaces as a bare TimeoutError, which
                # is URLError's sibling rather than its subclass.
                if attempt == MAX_ATTEMPTS:
                    raise JevError(
                        f"could not reach {self.provider.name}: "
                        f"{getattr(error, 'reason', error)}"
                    ) from error
            time.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
        raise JevError(f"{self.provider.name} could not be reached")
