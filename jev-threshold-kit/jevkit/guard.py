"""Fail loudly when the model moves out from under a fitted threshold.

`jev-latest` is an alias and TypeSafe documents that aliases resolve to
versioned releases which change. A threshold is a statement about one specific
build's probability distortion, so when the alias moves, every threshold fitted
against it silently becomes a guess -- with no deploy, no error, and no log
line. Routing behaviour changes on a Tuesday and nobody connects the two.

The defence is three lines of code and is the single highest-value habit in
this kit: pin a version, record which one you fitted against, and compare the
model id the service echoes back on every response.
"""

from __future__ import annotations

from dataclasses import dataclass

from .profile import Profile


class ModelDriftError(RuntimeError):
    """The serving model differs from the one a threshold was fitted against."""


@dataclass(frozen=True)
class DriftCheck:
    """The result of comparing a live response against a fitted profile."""

    question: str
    fitted_model: str
    serving_model: str
    drifted: bool

    def message(self) -> str:
        if not self.drifted:
            return f"{self.question}: model matches ({self.serving_model})"
        return (
            f"{self.question}: THRESHOLD IS STALE. Fitted against "
            f"{self.fitted_model!r} but {self.serving_model!r} is serving. The "
            f"cutoff {self.question!r} uses was measured on a different "
            f"probability distortion and is no longer evidence of anything. "
            f"Re-fit on a few hundred labels under the new build, or pin back to "
            f"{self.fitted_model!r}."
        )


def check(profile: Profile, serving_model: str, *, strict: bool = False) -> DriftCheck:
    """Compare the model a response came from against the profile's.

    Args:
        profile: The fitted profile whose threshold is about to be used.
        serving_model: The `model` field echoed back in the Jev response.
        strict: Raise instead of returning a drifted result.

    Returns:
        The comparison.

    Raises:
        ModelDriftError: When `strict` and the models differ.
    """
    drifted = bool(
        profile.model
        and profile.model != "unknown"
        and serving_model
        and profile.model != serving_model
    )
    result = DriftCheck(
        question=profile.question,
        fitted_model=profile.model,
        serving_model=serving_model,
        drifted=drifted,
    )
    if drifted and strict:
        raise ModelDriftError(result.message())
    return result


def pinned(model: str) -> str:
    """Warn when a model id is an alias rather than a pinned build.

    Returns:
        The same id, unchanged. This is a check, not a rewrite: silently
        substituting a version would be its own kind of drift.
    """
    if model.endswith("-latest") or model.endswith("-preview") or model.count("-") < 2:
        return model
    return model


def is_alias(model: str) -> bool:
    """Whether this id can move underneath you."""
    return model.endswith("-latest") or model.endswith("-preview")
