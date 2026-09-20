"""Trusted policy: the only thing in this system that can grant authority.

Policy is loaded from a trusted source, canonicalised, and FINGERPRINTED. Every decision binds
to the fingerprint it was made under, so a decision cannot be executed against a policy that
has changed underneath it.

The lesson this encodes, learned the hard way: an authority input read from a location the
acting agent can write is not an authority input. Policy must come from somewhere the agent
cannot reach, and the loader records a digest so tampering is detectable rather than assumed
away.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .verdict import Verdict


class PolicyError(ValueError):
    """The policy document cannot be used as an authority source."""


@dataclass(frozen=True)
class ActionRule:
    """What the deterministic layer permits for one action name."""

    action: str
    #: The MOST authority this action may ever have. Nothing can exceed it.
    ceiling: Verdict
    #: Resource patterns this action may touch. None means undeclared -> not permission.
    scope: tuple[str, ...] | None = None
    #: None means unknown. Unknown is never treated as "reversible".
    reversible: bool | None = None
    #: Whether this action is permitted to cause effects outside the system.
    external_side_effects: bool = False
    #: Force a human in the loop regardless of any other signal.
    requires_approval: bool = False


@dataclass(frozen=True)
class Policy:
    """A loaded, fingerprinted authority policy."""

    rules: Mapping[str, ActionRule]
    #: adapter name -> exact model id required. An alias here is refused at load.
    pinned_models: Mapping[str, str] = field(default_factory=dict)
    fingerprint: str = ""

    def rule_for(self, action: str) -> ActionRule | None:
        """The rule for an action, or None when the policy does not mention it."""
        return self.rules.get(action)


def _canonical(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def fingerprint(document: Mapping[str, Any]) -> str:
    """A stable digest of a policy document."""
    return hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def _verdict(raw: object, *, where: str) -> Verdict:
    if isinstance(raw, Verdict):
        return raw
    if not isinstance(raw, str):
        raise PolicyError(f"{where}: ceiling must be a verdict name, got {raw!r}")
    try:
        return Verdict[raw.strip().upper()]
    except KeyError:
        raise PolicyError(
            f"{where}: unknown verdict {raw!r}; expected one of "
            + ", ".join(v.name for v in sorted(Verdict))
        ) from None


def load(document: Mapping[str, Any]) -> Policy:
    """Build a Policy from a plain document, refusing anything ambiguous.

    Fails closed on malformed input: a policy that cannot be read exactly is not a policy.
    """
    if not isinstance(document, Mapping):
        raise PolicyError(f"policy must be a mapping, got {type(document).__name__}")
    raw_actions = document.get("actions")
    if not isinstance(raw_actions, Mapping) or not raw_actions:
        raise PolicyError("policy has no 'actions' mapping")

    rules: dict[str, ActionRule] = {}
    for name, raw in raw_actions.items():
        where = f"actions.{name}"
        if not isinstance(raw, Mapping):
            raise PolicyError(f"{where}: rule must be a mapping")
        scope_raw = raw.get("scope")
        scope: tuple[str, ...] | None = None
        if scope_raw is not None:
            from . import scope as scope_mod

            scope = tuple(scope_mod.normalize(scope_raw))
        reversible = raw.get("reversible")
        if reversible is not None and not isinstance(reversible, bool):
            raise PolicyError(f"{where}: reversible must be true, false or absent (unknown)")
        rules[str(name)] = ActionRule(
            action=str(name),
            ceiling=_verdict(raw.get("ceiling", "ESCALATE"), where=where),
            scope=scope,
            reversible=reversible,
            external_side_effects=bool(raw.get("external_side_effects", False)),
            requires_approval=bool(raw.get("requires_approval", False)),
        )

    pinned = document.get("pinned_models") or {}
    if not isinstance(pinned, Mapping):
        raise PolicyError("pinned_models must be a mapping of adapter -> model id")
    for adapter, model in pinned.items():
        if not isinstance(model, str) or not model.strip():
            raise PolicyError(f"pinned_models.{adapter}: model id must be a non-empty string")
        if is_alias(model):
            raise PolicyError(
                f"pinned_models.{adapter}: {model!r} is a moving alias, not a pinned build. "
                "A threshold or policy tied to an alias silently becomes a guess when it moves."
            )

    return Policy(
        rules=rules,
        pinned_models={str(k): str(v) for k, v in pinned.items()},
        fingerprint=fingerprint(document),
    )


def is_alias(model: str) -> bool:
    """Whether a model id can move underneath you.

    Carried from the Jev Threshold Kit's drift guard, which was correct and is reused here.
    """
    return model.endswith("-latest") or model.endswith("-preview")
