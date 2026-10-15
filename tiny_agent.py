# -*- coding: utf-8 -*-
"""A whole agent, in one file, with no API key and no network.

Companion code for the foundational session "What an Agent Actually Is".

The model here is a stub: a dictionary of canned decisions. That is deliberate.
Swap it for a real API call and nothing else in this file changes, which is the
point of the session: the loop, the tools, the ceiling and the history are YOUR
code. The model is one function call inside it.

Run it:

    python tiny_agent.py
    python tiny_agent.py --max-turns 2          # watch the ceiling bite
    python tiny_agent.py --no-history           # watch coherence break
    python tiny_agent.py --question "refund"
"""
from __future__ import annotations

import argparse
import json


# --- The tools. Plain functions. This is the whole mystery. -----------------
def lookup_policy(topic: str) -> str:
    table = {
        "refund": "Refunds are accepted within 30 days of delivery.",
        "shipping": "Standard shipping takes 3 to 5 business days.",
        "warranty": "Hardware carries a 24 month warranty.",
    }
    return table.get(topic, "No policy found for %r." % topic)


def lookup_order(order_id: str) -> str:
    table = {
        "A-1001": "Order A-1001: delivered 12 days ago, 1 keyboard, 149.00.",
        "A-1002": "Order A-1002: shipped yesterday, 1 monitor, 890.00.",
    }
    return table.get(order_id, "No order %r." % order_id)


TOOLS = {"lookup_policy": lookup_policy, "lookup_order": lookup_order}


# --- The "model". Replace this function and you have a real agent. ----------
# A real call is: client.messages.create(model=..., messages=history, tools=...)
# It returns either a tool call or a final answer. That is the only contract the
# loop depends on, which is why the stub can stand in for it here.
SCRIPT = {
    "refund": [
        {"tool": "lookup_order", "arg": "A-1001",
         "thought": "I need the order date before I can judge the refund window."},
        {"tool": "lookup_policy", "arg": "refund",
         "thought": "Now I need the policy to compare against."},
        {"answer": "Order A-1001 was delivered 12 days ago and the refund window "
                   "is 30 days, so the refund can be processed."},
    ],
    "warranty": [
        {"tool": "lookup_policy", "arg": "warranty",
         "thought": "Warranty length is a policy question."},
        {"answer": "Hardware is covered for 24 months from delivery."},
    ],
}


def fake_model(history: list, question: str) -> dict:
    """Returns the next decision. Depends ONLY on the history it is given, which
    is what makes the --no-history demo work: strip the history and the model
    loses track of which step it is on and repeats itself forever."""
    steps = SCRIPT.get(question, SCRIPT["warranty"])
    # How many tool results has it already seen? That is its sense of progress.
    seen = sum(1 for m in history if m["role"] == "tool")
    return steps[min(seen, len(steps) - 1)]


# --- The loop. This is the agent. -------------------------------------------
def run(question: str, max_turns: int = 6, keep_history: bool = True,
        tokens_per_message: int = 500, verbose: bool = True) -> dict:
    """The five decisions of the session, all visible in this function:

    1. Orchestration pattern: one agent, one loop. No router, no supervisor.
    2. Role, and therefore model tier: this agent uses tools across several
       steps, which is the most demanding class of work there is.
    3. Iteration ceiling: max_turns. Without it, a bad decision loops forever.
    4. Who carries the history: keep_history. Replaying it is what makes the
       agent coherent, and it is also what makes input grow every single turn.
    5. Who validates: see the check at the end. Code, not another model call.
    """
    system = "You are a support agent. Use the tools before answering."
    history = [{"role": "system", "text": system, "tokens": tokens_per_message}]
    history.append({"role": "user", "text": question, "tokens": tokens_per_message})

    billed_input = 0
    turns = 0
    answer = None
    trace = []

    while turns < max_turns:
        turns += 1
        # Decision 4 in action: what we send is the WHOLE history, every turn.
        sent = history if keep_history else history[-1:]
        read_now = sum(m["tokens"] for m in sent)
        billed_input += read_now

        decision = fake_model(sent, question)
        trace.append({"turn": turns, "read": read_now, "billed": billed_input,
                      "action": decision.get("tool") or "final answer"})
        if verbose:
            print("  turn %d: reads %6s tokens (billed %7s) -> %s"
                  % (turns, f"{read_now:,}", f"{billed_input:,}",
                     decision.get("tool") or "final answer"))

        if "answer" in decision:
            answer = decision["answer"]
            break

        tool = TOOLS[decision["tool"]]
        result = tool(decision["arg"])
        if verbose:
            print("           %s(%r) -> %s" % (decision["tool"], decision["arg"], result))
        history.append({"role": "assistant", "text": decision["thought"],
                        "tokens": tokens_per_message})
        history.append({"role": "tool", "text": result, "tokens": tokens_per_message})

    # Decision 5: validation is CODE. Cheap, deterministic, and it can say no.
    # Validating with another model call hands back the cost you were avoiding.
    hit_ceiling = answer is None
    valid = bool(answer) and len(answer) > 20

    naive_estimate = tokens_per_message * 2 * turns
    return {
        "question": question,
        "turns": turns,
        "hit_ceiling": hit_ceiling,
        "answer": answer,
        "valid": valid,
        "billed_input_tokens": billed_input,
        "naive_estimate_tokens": naive_estimate,
        "overspend_pct": round((billed_input / naive_estimate - 1) * 100, 1)
        if naive_estimate else 0.0,
        "trace": trace,
    }


def self_test() -> int:
    n = 0
    r = run("refund", verbose=False)
    assert r["turns"] == 3, r["turns"]; n += 1
    assert r["valid"] is True; n += 1
    assert "30 days" in r["answer"]; n += 1
    # Every turn reads more than the one before it. That is the whole lesson.
    reads = [t["read"] for t in r["trace"]]
    assert reads == sorted(reads) and len(set(reads)) == len(reads), reads; n += 1
    assert r["billed_input_tokens"] > reads[0] * r["turns"] / 2; n += 1

    # The ceiling stops the loop and says so, instead of running forever.
    r2 = run("refund", max_turns=2, verbose=False)
    assert r2["hit_ceiling"] is True; n += 1
    assert r2["answer"] is None; n += 1
    assert r2["valid"] is False; n += 1

    # Drop the history and the agent loses the thread. It does not repeat turn
    # one: it gets STUCK on step two, calling the same tool until the ceiling
    # stops it, because with only the last message in the window it can never
    # tell that it already has what it needs.
    r3 = run("refund", keep_history=False, verbose=False)
    assert r3["hit_ceiling"] is True, r3; n += 1
    actions = [t["action"] for t in r3["trace"]]
    assert actions[-3:] == ["lookup_policy"] * 3, actions; n += 1
    assert len(set(actions)) < len(actions), actions; n += 1
    assert "final answer" not in actions, actions; n += 1
    # Every turn reads the same amount now, so the loop looks flat and healthy
    # on a per turn basis while never producing an answer.
    assert len({t["read"] for t in r3["trace"]}) == 1, r3["trace"]; n += 1
    # And it is cheaper per turn, which is exactly the trap: the broken agent
    # looks better on the invoice.
    assert r3["trace"][-1]["read"] < r["trace"][-1]["read"]; n += 1

    print("tiny_agent self-test OK (%d asserts)" % n)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="One agent, one file, no API key.")
    p.add_argument("--question", default="refund", choices=sorted(SCRIPT))
    p.add_argument("--max-turns", type=int, default=6)
    p.add_argument("--no-history", action="store_true",
                   help="stop replaying the history and watch coherence break")
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()

    if a.self_test:
        raise SystemExit(self_test())

    print("question: %s | ceiling: %d turns | history replayed: %s\n"
          % (a.question, a.max_turns, not a.no_history))
    res = run(a.question, max_turns=a.max_turns, keep_history=not a.no_history,
              verbose=not a.json)
    if a.json:
        print(json.dumps(res, indent=2))
        raise SystemExit(0)

    print()
    if res["hit_ceiling"]:
        print("  HIT THE CEILING after %d turns with no answer." % res["turns"])
        print("  This is the good failure. Without a ceiling this runs until")
        print("  someone notices the invoice.")
    else:
        print("  answer: %s" % res["answer"])
        print("  validated by code: %s" % res["valid"])
    print("\n  billed %s input tokens across %d turns, against the %s a flat"
          % (f"{res['billed_input_tokens']:,}", res["turns"],
             f"{res['naive_estimate_tokens']:,}"))
    print("  per turn estimate would predict: %+.1f%%." % res["overspend_pct"])
