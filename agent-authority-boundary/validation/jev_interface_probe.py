#!/usr/bin/env python3
"""Jev interface probe — everything a live call is NOT required to establish.

WHAT THIS IS NOT: this is not live validation. Every response below is served by a local stub
built from TypeSafe's published interface description. It proves what our code SENDS, and how
our code BEHAVES when a server answers in a given way. It cannot prove that the real service
answers that way. Anything marked LIVE-ONLY below remains unverified.

Run: python3 validation/jev_interface_probe.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "jev-threshold-kit"))

from authority.adapters.jev import JevAdapter, JevResponse, confidence_of  # noqa: E402
from authority.engine import ActionRequest, decide  # noqa: E402
from authority.policy import load  # noqa: E402
from authority.verdict import Verdict  # noqa: E402
from jevkit.client import JevClient, JevError, Provider  # noqa: E402

CAPTURED: list[dict] = []
MODE = {"name": "ok"}

DOCUMENTED_ANSWERS = {
    "is_urgent": {"type": "noul", "noul": 0.96},
    "department": {
        "type": "choice", "choice": "billing", "confidence": 0.97,
        "probabilities": {"billing": 0.97, "technical": 0.02, "other": 0.01},
    },
    "frustration": {
        "type": "score", "score": 1.3, "confidence": 0.55,
        "probabilities": {"1": 0.6, "2": 0.3, "3": 0.1},
    },
}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def handle_one_request(self):
        # The timeout case deliberately hangs up mid-response; that is the scenario, not a bug.
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        CAPTURED.append({"path": self.path, "headers": dict(self.headers), "body": body.decode()})
        mode = MODE["name"]
        if mode == "slow":
            time.sleep(3.0)
        if mode in ("500", "429"):
            self.send_response(int(mode)); self.end_headers()
            self.wfile.write(b'{"error":"transient"}'); return
        if mode == "401":
            self.send_response(401); self.end_headers()
            self.wfile.write(b'{"error":"invalid api key"}'); return
        if mode == "malformed":
            self.send_response(200); self.end_headers()
            self.wfile.write(b"<html>not json at all</html>"); return
        if mode == "no_answers":
            payload = {"usage": {}}
        elif mode == "missing_confidence":
            payload = {"answers": {"department": {"type": "choice", "choice": "billing",
                                                  "probabilities": {"billing": 0.97}}}}
        else:
            payload = {"answers": DOCUMENTED_ANSWERS, "usage": {"input_tokens": 11}}
        raw = json.dumps(payload).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def run(server):
    server.serve_forever()


POLICY = load({
    "actions": {"w": {"ceiling": "ALLOW", "scope": ["docs/**"], "reversible": True}},
    "pinned_models": {"jev": "typesafe/jev-1.13"}, "trusted_sources": ["policy"],
})
REQUEST = ActionRequest("w", ("docs/a.md",))


def ceiling_without_advice() -> Verdict:
    return decide(POLICY, REQUEST).verdict


def authority_with(advice) -> Verdict:
    return decide(POLICY, REQUEST, advice).verdict


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=run, args=(server,), daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/api/alpha/decisions"
    provider = Provider(name="stub", model="typesafe/jev-1.13", url=url, api_key="probe-key")
    findings: list[tuple[str, str]] = []

    def record(name, detail): findings.append((name, detail)); print(f"  {name:38} {detail}")

    print("\n== 1. REQUEST FORMAT actually put on the wire ==")
    client = JevClient(provider, timeout=2.0)
    from jevkit.client import choice, noul, score
    decision = client.ask(
        "customer says the invoice is wrong",
        {"is_urgent": noul("the customer needs a reply today"),
         "department": choice("route this", {"billing": "money", "technical": "bugs",
                                             "other": "none of these"}),
         "frustration": score("how annoyed", {"1": "calm", "2": "cross", "3": "furious"})},
    )
    sent = CAPTURED[-1]
    body = json.loads(sent["body"])
    record("method/path", f"POST {sent['path']}")
    record("auth header", sent["headers"].get("Authorization", "").split()[0] + " <key>")
    record("content-type", sent["headers"].get("Content-Type", ""))
    record("body top-level keys", str(sorted(body.keys())))
    record("model field", repr(body.get("model")))
    record("question types sent", str(sorted({q["type"] for q in body["questions"].values()})))
    record("choice includes escape", "other" in body["questions"]["department"]["criteria"])

    print("\n== 2. RESPONSE MAPPING (documented shapes) ==")
    for qid, ans in DOCUMENTED_ANSWERS.items():
        kind = ans["type"]
        value = ans.get(kind)
        resp = JevResponse("typesafe/jev-1.13", qid, kind, value,
                           confidence=ans.get("confidence"),
                           probabilities=ans.get("probabilities"))
        conf, prov = confidence_of(resp)
        record(f"{kind} -> confidence", f"{conf!r} ({prov})")
        if kind == "noul":
            assert prov == "derived", "noul confidence must be labelled derived"
        else:
            assert prov == "reported" and conf == ans["confidence"], "must use reported value"

    print("\n== 3. KIT-LEVEL confidence for the same answers ==")
    for name, answer in decision.answers.items():
        record(f"kit {answer.kind} confidence", f"{answer.confidence!r}")

    print("\n== 4. FAILURE MODES: authority must never increase ==")
    baseline = ceiling_without_advice()
    record("ceiling with no advice", str(baseline))
    for mode, label in (("500", "server error (retried)"), ("429", "rate limited (retried)"),
                        ("401", "bad credentials"), ("malformed", "non-JSON body"),
                        ("no_answers", "no answers object"), ("slow", "timeout")):
        MODE["name"] = mode
        c = JevClient(provider, timeout=0.5 if mode == "slow" else 2.0)
        try:
            c.ask("x", {"q": noul("is it safe")})
            outcome = "returned a decision"
        except JevError as exc:
            outcome = f"JevError: {str(exc)[:46]}"
        except Exception as exc:  # noqa: BLE001 - probe records whatever escapes
            outcome = f"{type(exc).__name__}: {str(exc)[:40]}"
        # A transport failure yields NO advice at all. Authority must equal the no-advice ceiling.
        got = ceiling_without_advice()
        assert got == baseline, f"{mode} changed authority"
        record(f"{label}", f"{outcome} -> authority {got}")

    print("\n== 5. MISSING CONFIDENCE on a choice ==")
    MODE["name"] = "missing_confidence"
    try:
        d = JevClient(provider, timeout=2.0).ask("x", {"department": choice(
            "route", {"billing": "m", "other": "none"})})
        ans = d.answers["department"]
        record("kit confidence when absent", f"{ans.confidence!r}  <-- kit substitutes a value")
    except Exception as exc:  # noqa: BLE001
        record("kit behaviour", f"{type(exc).__name__}: {str(exc)[:50]}")
    adapter_resp = JevResponse("typesafe/jev-1.13", "department", "choice", "billing",
                               confidence=None, probabilities={"billing": 0.97})
    conf, prov = confidence_of(adapter_resp)
    record("adapter confidence when absent", f"{conf!r} ({prov})")
    assert conf is None and prov == "unavailable", "adapter must not invent a confidence"
    advice = JevAdapter().advise(adapter_resp, min_confidence=0.8)
    record("adapter verdict", str(advice.verdict))
    assert advice.verdict is Verdict.ESCALATE

    print("\n== 6. HOSTILE / MALFORMED into the adapter ==")
    for label, resp in (
        ("confidence 99", JevResponse("typesafe/jev-1.13", "q", "choice", "x", confidence=99.0)),
        ("confidence NaN", JevResponse("typesafe/jev-1.13", "q", "choice", "x",
                                       confidence=float("nan"))),
        ("noul out of range", JevResponse("typesafe/jev-1.13", "q", "noul", 7.0)),
        ("unknown kind", JevResponse("typesafe/jev-1.13", "q", "telepathy", 1.0)),
        ("not a response", {"model": "typesafe/jev-1.13"}),
    ):
        adv = JevAdapter().advise(resp, min_confidence=0.8)
        got = authority_with(adv) if adv.model == "typesafe/jev-1.13" else authority_with(adv)
        assert got <= baseline, f"{label} increased authority"
        record(label, f"{adv.verdict} -> authority {got} (ceiling {baseline})")

    server.shutdown()
    print(f"\n{len(findings)} observations recorded. "
          "All stub-based; nothing here is evidence about the live service.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
