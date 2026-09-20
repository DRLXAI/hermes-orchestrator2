# Jev integration validation — result: BLOCKED at the credential/network boundary

**No live call was made. Nothing in this document is evidence about the real Jev service.**

## Why it is blocked — two independent reasons

**1. No credentials.** `jevkit.client.resolve_provider()` reads `TYPESAFE_API_KEY` (direct API,
`POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`) or `OPENROUTER_API_KEY`
(`POST https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13`). Neither is set in
this environment, and there is no `~/.netrc`, `~/.typesafe` or equivalent.

**2. Network egress is denied to both hosts**, so credentials alone would not be enough. The
agent proxy logged, for this session:

```
connect_rejected  api.typesafe.ai:443   gateway answered 403 to CONNECT (policy denial)
connect_rejected  openrouter.ai:443     gateway answered 403 to CONNECT (policy denial)
```

A control host (`github.com`) tunnels normally, so this is policy, not an outage.

## What is needed to unblock

1. An API key in the environment of whoever runs it — `OPENROUTER_API_KEY` is the realistic one
   (TypeSafe's direct API was waitlisted at launch); `TYPESAFE_API_KEY` if a direct key exists.
2. Egress allowed to `openrouter.ai:443` and/or `api.typesafe.ai:443`.
3. Then: `python3 validation/jev_interface_probe.py --live` (the `--live` path is not written,
   because writing an unrunnable code path is not validation).

Both are outside what this session can grant itself.

## What WAS established without the service

`validation/jev_interface_probe.py` runs the real `jevkit` client against a local stub built
from the published interface description. It proves what our code **sends** and how our code
**behaves** when a server answers in a given way. It cannot prove the real service answers that
way.

### Request format actually put on the wire (real client, stub server)

```
POST /api/alpha/decisions
Authorization: Bearer <key>
Content-Type: application/json
body keys: ["model", "questions", "state"]
model: "typesafe/jev-1.13"
question types: choice, noul, score
```

### Response mapping, documented shapes

| Primitive | Server field | Adapter confidence | Provenance |
|---|---|---|---|
| `noul` | `noul: 0.96` (bare probability, **no confidence field**) | 0.92 | `derived` |
| `choice` | `choice` + `confidence: 0.97` + `probabilities` | 0.97 | `reported` |
| `score` | `score` + `confidence: 0.55` + `probabilities` | 0.55 | `reported` |

The adapter uses the server's value for choice/score and never substitutes `max(probabilities)`.
For `noul` it derives `|p − 0.5| × 2` and labels it `derived` — a symmetric distance from a coin
flip, not P(correct).

### Failure modes — authority never increased in any case

Ceiling with no advice: `ALLOW`. After each failure the authority was still `ALLOW`, because a
transport failure produces **no advice at all** rather than permissive advice.

| Condition | Client behaviour | Resulting authority |
|---|---|---|
| HTTP 500 (retried 3×) | `JevError` | ALLOW (unchanged) |
| HTTP 429 (retried 3×) | `JevError` | ALLOW (unchanged) |
| HTTP 401 bad key | `JevError`, no retry | ALLOW (unchanged) |
| Non-JSON body | `JevError` | ALLOW (unchanged) |
| Missing `answers` object | `JevError` | ALLOW (unchanged) |
| Read timeout | `JevError` | ALLOW (unchanged) |

### Hostile input straight into the adapter

| Input | Advice | Authority (ceiling ALLOW) |
|---|---|---|
| `confidence=99.0` | ESCALATE | ESCALATE |
| `confidence=NaN` | ESCALATE | ESCALATE |
| `noul` value 7.0 | ESCALATE | ESCALATE |
| unknown primitive | ESCALATE | STOP (unpinned model) |
| not a `JevResponse` | ESCALATE | STOP (unpinned model) |

## FINDING — the kit invents a confidence; the adapter does not

`jevkit/client.py:150` and `:159` parse choice and score with:

```python
confidence=float(payload.get("confidence", 0.0))
```

A **missing** confidence silently becomes `0.0`, indistinguishable from a genuine
maximally-unsure answer. Observed in the probe: the kit reported `0.0`, the adapter reported
`None` / `unavailable`.

For **gating** this fails safe (0.0 clears no threshold). For **calibration** it is a data
integrity problem: a fabricated 0.0 paired with a correct outcome would drag a fitted curve, and
nothing downstream can tell it from a real one. This is in the kit, not in the authority
boundary, and is not fixed here — recorded for the reviewer and for the kit's own backlog.

## LIVE-ONLY — still unverified

- That the real service returns the envelope `{"answers": {...}}` with these per-primitive fields.
- Real authentication, rate limits, and error bodies.
- Whether a real `choice`/`score` answer can ever arrive without a `confidence` field.
- Real timeout behaviour and latency.
- Whether `typesafe/jev-1.13` on OpenRouter and `jev-latest` on TypeSafe differ in response shape.
- Anything about accuracy, calibration or performance. **No such claim is made anywhere.**
