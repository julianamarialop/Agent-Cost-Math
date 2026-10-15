# -*- coding: utf-8 -*-
"""Organizational context: three sources, one answer, and the four ways it goes
wrong quietly.

Companion code for two sessions:
  100  Your Agent Is Not Missing Intelligence. It Is Missing Context.
  200  When Two Sources Disagree, Your Agent Picks the One You Pasted First

An agent answering a real business question needs three different kinds of
context, and they are not interchangeable:

  work signals      who met whom, which thread is live, who owns the account
  business data     the certified numbers, with grain and validity
  knowledge         policies and documents, which carry authority and a date

They have different owners, different permissions, different freshness and
different failure modes. Treating them as one bucket of text to paste into a
prompt is the mistake this file exists to demonstrate.

    python org_context.py
    python org_context.py --user ae
    python org_context.py --no-precedence
    python org_context.py --missing data
    python org_context.py --budget
    python org_context.py --self-test
"""
from __future__ import annotations

import argparse
import json

# ---------------------------------------------------------------------------
# The three sources
# ---------------------------------------------------------------------------
# Every entry declares what an agent cannot infer: who owns it, who may read it,
# how fresh it is, and how much authority it carries. A prompt that concatenates
# text loses all four.
WORK = [
    {"id": "w1", "kind": "meeting", "text": "Renewal call with Northwind booked "
     "for Thursday, owner is the named account executive",
     "age_days": 1, "visible_to": ["ae", "director", "finance"],
     "authority": "signal", "tokens": 180},
    {"id": "w2", "kind": "thread", "text": "Contoso thread went quiet 19 days ago "
     "after the security questionnaire",
     "age_days": 19, "visible_to": ["ae", "director", "finance"],
     "authority": "signal", "tokens": 210},
]

DATA = [
    {"id": "d1", "kind": "certified_metric", "text": "Northwind annual revenue "
     "1.2M, churn risk score 0.71",
     "age_days": 1, "visible_to": ["ae", "director", "finance"],
     "authority": "certified", "grain": "one row per account per day",
     "tokens": 90},
    {"id": "d2", "kind": "certified_metric", "text": "Contoso annual revenue "
     "3.4M, churn risk score 0.22",
     "age_days": 1, "visible_to": ["ae", "director", "finance"],
     "authority": "certified", "grain": "one row per account per day",
     "tokens": 90},
    {"id": "d3", "kind": "restricted_metric", "text": "Northwind gross margin "
     "11%, Contoso gross margin 34%",
     "age_days": 1, "visible_to": ["finance"],
     "authority": "certified", "grain": "one row per account per quarter",
     "tokens": 80},
]

KNOWLEDGE = [
    {"id": "k1", "kind": "policy", "text": "Account prioritization policy: rank "
     "by annual revenue.",
     "age_days": 640, "visible_to": ["ae", "director", "finance"],
     "authority": "policy", "effective": "published, no end date", "tokens": 320},
    {"id": "k2", "kind": "directive", "text": "Quarterly directive from the "
     "regional director: this quarter, rank by churn risk ahead of revenue.",
     "age_days": 12, "visible_to": ["ae", "director", "finance"],
     "authority": "directive", "effective": "current quarter", "tokens": 260},
]

SOURCES = {"work": WORK, "data": DATA, "knowledge": KNOWLEDGE}

# The question, and what answering it honestly REQUIRES. Declaring the required
# sources is what turns a partial answer from an invisible problem into a refusal.
QUESTION = "which account should I prioritize this week"
REQUIRED_SOURCES = ("work", "data", "knowledge")

# Declared precedence. Without it the agent has no principle to resolve a
# conflict by, so it falls back on whatever the retrieval order happened to be.
PRECEDENCE = ["directive", "policy", "certified", "signal"]


def _visible(item: dict, user: str) -> bool:
    return user in item["visible_to"]


def retrieve(user: str, drop: tuple = ()) -> dict:
    """What this user is allowed to see, per source."""
    out = {}
    withheld = []
    for name, items in SOURCES.items():
        if name in drop:
            out[name] = []
            continue
        allowed, blocked = [], []
        for it in items:
            (allowed if _visible(it, user) else blocked).append(it)
        out[name] = allowed
        withheld.extend(blocked)
    return {"available": out, "withheld": withheld}


def detect_conflicts(items: list) -> list:
    """A conflict is two pieces of context that would drive different answers.

    Here: the standing policy ranks by revenue, the quarterly directive ranks by
    churn risk, and they select different accounts. Nothing in the text says
    which one wins, which is exactly the situation the precedence list exists for.
    """
    rules = [i for i in items if i["authority"] in ("policy", "directive")]
    conflicts = []
    for a in rules:
        for b in rules:
            if a["id"] < b["id"] and a["authority"] != b["authority"]:
                conflicts.append({
                    "between": [a["id"], b["id"]],
                    "authorities": [a["authority"], b["authority"]],
                    "note": "these rank accounts by different fields",
                })
    return conflicts


def answer(user: str = "director", use_precedence: bool = True,
           drop: tuple = (), retrieval_order: list | None = None) -> dict:
    """Answer the question, and report everything the answer depends on.

    The report is the product. An answer without its sources, its conflicts and
    its omissions is not auditable, and an agent that produces one has no way to
    tell the reader that it was working from half the picture.
    """
    got = retrieve(user, drop)
    available = got["available"]

    missing = [s for s in REQUIRED_SOURCES if not available.get(s)]
    if missing:
        # Refuse rather than answer from what happens to be present. A partial
        # answer is indistinguishable from a complete one on the way out.
        return {
            "answered": False,
            "reason": "required context source(s) unavailable: %s. Answering from "
                      "the remaining sources would produce a confident answer "
                      "built on part of the picture, and nothing in the output "
                      "would say so." % ", ".join(missing),
            "missing_sources": missing,
            "used": [],
            "conflicts": [],
            "withheld_count": len(got["withheld"]),
        }

    items = [i for src in REQUIRED_SOURCES for i in available[src]]
    if retrieval_order:
        rank = {i: n for n, i in enumerate(retrieval_order)}
        items = sorted(items, key=lambda i: rank.get(i["id"], 99))

    conflicts = detect_conflicts(items)
    rules = [i for i in items if i["authority"] in ("policy", "directive")]

    if conflicts and not use_precedence:
        # This is the failure the 200 session is about. With no declared
        # precedence the agent still answers, and it resolves the conflict by
        # position: whatever the retrieval put first wins. The answer is stable
        # looking and arbitrary.
        winner = rules[0]
        basis = "retrieval order, because no precedence is declared"
    else:
        winner = min(rules, key=lambda i: PRECEDENCE.index(i["authority"]))
        basis = "declared precedence: %s" % " > ".join(PRECEDENCE)

    if "churn" in winner["text"]:
        chosen, field = "Northwind", "churn risk score 0.71 against 0.22"
    else:
        chosen, field = "Contoso", "annual revenue 3.4M against 1.2M"

    # Oversharing check: the answer must not reveal that a withheld item exists.
    # "There is a margin report you cannot see" leaks the report.
    withheld_ids = [i["id"] for i in got["withheld"]]

    return {
        "answered": True,
        "account": chosen,
        "because": field,
        "rule_applied": winner["id"],
        "rule_authority": winner["authority"],
        "resolution_basis": basis,
        "used": [i["id"] for i in items],
        "used_tokens": sum(i["tokens"] for i in items),
        "conflicts": conflicts,
        "conflict_resolved_by_position": bool(conflicts and not use_precedence),
        "withheld_count": len(withheld_ids),
        "withheld_ids": withheld_ids,
        "citation": "%s (%s, %s)" % (winner["id"], winner["authority"],
                                     winner.get("effective", "no validity stated")),
    }


def context_budget(user: str = "director") -> dict:
    """Everything available against only what the question requires.

    Organizational context is not free. Injecting every source the agent can
    reach is the intuitive move and it pays twice: more tokens per call, and a
    wider window competing for attention.
    """
    got = retrieve(user)
    everything = [i for items in got["available"].values() for i in items]
    needed = [i for src in REQUIRED_SOURCES for i in got["available"][src]]
    all_tokens = sum(i["tokens"] for i in everything)
    need_tokens = sum(i["tokens"] for i in needed)
    return {
        "items_reachable": len(everything),
        "tokens_if_everything_injected": all_tokens,
        "items_required": len(needed),
        "tokens_required": need_tokens,
        "note": "on this question the two are close, which is the honest result: "
                "the saving from selection grows with the corpus, and the reason "
                "to select is precedence and permission long before it is cost",
    }


def self_test() -> int:
    n = 0

    # The correct answer: the current directive beats the standing policy, and
    # the answer cites which rule it applied.
    r = answer()
    assert r["answered"] is True; n += 1
    assert r["account"] == "Northwind", r; n += 1
    assert r["rule_authority"] == "directive"; n += 1
    assert "declared precedence" in r["resolution_basis"]; n += 1
    assert r["citation"].startswith("k2 (directive"); n += 1
    # The conflict is detected and reported even when it is resolved. A resolved
    # conflict the reader never sees is a decision made on their behalf.
    assert len(r["conflicts"]) == 1, r["conflicts"]; n += 1

    # The 200 headline: same data, no declared precedence, and the answer flips
    # purely on retrieval order. Both runs look equally confident.
    a = answer(use_precedence=False, retrieval_order=["k1", "k2"])
    b = answer(use_precedence=False, retrieval_order=["k2", "k1"])
    assert a["account"] == "Contoso", a; n += 1
    assert b["account"] == "Northwind", b; n += 1
    assert a["account"] != b["account"]; n += 1
    assert a["conflict_resolved_by_position"] is True; n += 1
    assert "retrieval order" in a["resolution_basis"]; n += 1
    # Precedence makes the answer independent of retrieval order, which is the
    # actual deliverable of the session.
    p1 = answer(retrieval_order=["k1", "k2"])
    p2 = answer(retrieval_order=["k2", "k1"])
    assert p1["account"] == p2["account"] == "Northwind"; n += 1

    # Permission is resolved per user, not per agent. The account executive
    # cannot see the margin report; the finance user can.
    ae = answer(user="ae")
    fin = answer(user="finance")
    assert ae["withheld_count"] == 1, ae; n += 1
    assert ae["withheld_ids"] == ["d3"]; n += 1
    assert fin["withheld_count"] == 0; n += 1
    # Both still answer, and both reach the same account, because the withheld
    # item was never what decided it. Trimming by identity does not have to
    # degrade the answer, and assuming it does is why teams skip it.
    assert ae["account"] == fin["account"] == "Northwind"; n += 1
    # The agent's own reachability is wider than the user's, so the identity that
    # matters is the USER's. This is the field that makes that explicit.
    assert len(fin["used"]) > len(ae["used"]); n += 1

    # Oversharing: the withheld item must not appear in what was used.
    assert "d3" not in ae["used"]; n += 1
    # And nothing in the answer text names it. The count is internal telemetry,
    # not something to tell the user, because "there is a report you cannot see"
    # leaks the report.
    assert "margin" not in json.dumps(
        {k: v for k, v in ae.items() if k not in ("withheld_ids",)}); n += 1

    # A missing required source is a refusal, not a partial answer.
    for src in ("work", "data", "knowledge"):
        res = answer(drop=(src,))
        assert res["answered"] is False, src; n += 1
        assert res["missing_sources"] == [src]; n += 1
        assert "part of the picture" in res["reason"]; n += 1

    # Freshness is a property of the item, and the old policy is very old.
    k1 = [i for i in KNOWLEDGE if i["id"] == "k1"][0]
    k2 = [i for i in KNOWLEDGE if i["id"] == "k2"][0]
    assert k1["age_days"] > 600 and k2["age_days"] < 30; n += 1
    # But recency is NOT the tie breaker: authority is. A recent email that
    # contradicts policy does not win by being recent, it wins by being a
    # directive with a stated validity.
    assert PRECEDENCE.index("directive") < PRECEDENCE.index("policy"); n += 1
    assert PRECEDENCE.index("certified") < PRECEDENCE.index("signal"); n += 1

    # Budget: reachable against required.
    b = context_budget()
    assert b["items_reachable"] >= b["items_required"]; n += 1
    assert b["tokens_required"] <= b["tokens_if_everything_injected"]; n += 1

    print("org_context self-test OK (%d asserts)" % n)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Three context sources, one answer, and what it depends on.")
    p.add_argument("--user", default="director", choices=["ae", "director", "finance"])
    p.add_argument("--no-precedence", action="store_true",
                   help="drop the precedence list and let retrieval order decide")
    p.add_argument("--missing", choices=["work", "data", "knowledge"],
                   help="remove a required source and watch it refuse")
    p.add_argument("--budget", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()

    if a.self_test:
        raise SystemExit(self_test())

    if a.budget:
        b = context_budget(a.user)
        print(json.dumps(b, indent=2) if a.json else
              "reachable %d items, %s tokens\nrequired  %d items, %s tokens\n%s"
              % (b["items_reachable"], f"{b['tokens_if_everything_injected']:,}",
                 b["items_required"], f"{b['tokens_required']:,}", b["note"]))
        raise SystemExit(0)

    print('question: "%s?"' % QUESTION)
    print("asked by: %s | precedence declared: %s\n"
          % (a.user, not a.no_precedence))

    if a.no_precedence:
        for order in (["k1", "k2"], ["k2", "k1"]):
            res = answer(a.user, use_precedence=False, retrieval_order=order)
            print("  retrieval order %s -> %s (%s)"
                  % (order, res["account"], res["because"]))
            print("     resolved by: %s" % res["resolution_basis"])
        print("\n  Same sources, same question, two answers. Nothing in either")
        print("  output signals that the choice was arbitrary.")
        raise SystemExit(0)

    res = answer(a.user, drop=(a.missing,) if a.missing else ())
    if a.json:
        print(json.dumps(res, indent=2))
        raise SystemExit(0)

    if not res["answered"]:
        print("  REFUSES to answer")
        print("  %s" % res["reason"])
        raise SystemExit(0)

    print("  answer: prioritize %s" % res["account"])
    print("  because: %s" % res["because"])
    print("  rule applied: %s" % res["citation"])
    print("  resolved by: %s" % res["resolution_basis"])
    print("  context used: %s (%s tokens)"
          % (", ".join(res["used"]), f"{res['used_tokens']:,}"))
    for c in res["conflicts"]:
        print("  conflict reported: %s and %s (%s) %s"
              % (c["between"][0], c["between"][1], " against ".join(c["authorities"]),
                 c["note"]))
    if res["withheld_count"]:
        print("  withheld by permission: %d item(s), not used and not named to "
              "the user" % res["withheld_count"])
