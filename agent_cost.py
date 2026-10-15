# -*- coding: utf-8 -*-
"""Agent cost math: price an agentic architecture before you build it.

Standalone, stdlib only. No network required, except for --live, which prices
from the vendors' public sources instead of the hand table. Companion code for
the talk "Agent Cost Is an Architecture Decision, Not a Token Count".

Three ideas the code exists to make undeniable:

1. Context inflation is quadratic. A stateless agent rereads its whole history
   every cycle, so input does not grow linearly with the number of steps.
2. The role decides the model tier, and the task class can override it upward.
3. An estimator that guesses is worse than one that refuses. Missing iteration
   ceiling, missing volume and unknown role all stop the run and name what is
   missing, because a line that enters an estimate at zero disappears from the
   proposal and nobody notices the absence.

Run the demos:

    python agent_cost.py --architecture architecture.example.json
    python agent_cost.py --architecture architecture.example.json --live
    python agent_cost.py --inflation 2000 500 10
    python agent_cost.py --cascade 1 5 0.60 100
    python agent_cost.py --roles
    python agent_cost.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys

TIER_ORDER = ["cheap", "mid", "high"]

# ---------------------------------------------------------------------------
# 1. The role decides the tier
# ---------------------------------------------------------------------------
# The rule this table operationalizes: never use a brilliant model for a trivial
# task. "Trivial" is not a feeling, it is the position the component occupies in
# the architecture. Each entry carries the reason, because a rule whose reason is
# hidden gets obeyed or ignored, and both are bad in an estimate that ships to a
# client.
ROLE_TIERS = {
    "router": ("cheap", "classifies which specialist the request falls to; the "
               "entry point of the system cannot burn expensive tokens"),
    "specialist": ("mid", "takes a request end to end with the tools of one domain"),
    "supervisor": ("mid", "breaks work into a plan, delegates and evaluates; "
                   "reasoning about reasoning"),
    "director": ("high", "broad strategic mandate; the only role that justifies "
                 "a frontier model on the front line"),
    "manager": ("mid", "orchestrates dozens of operational workers"),
    "operational": ("cheap", "one skill with a clear instruction, at volume and "
                    "in parallel"),
    "extractor": ("cheap", "Map phase: reads a chunk and copies out the relevant "
                  "span"),
    "compressor": ("cheap", "summarizes history backstage; the reader is the next "
                   "iteration of the agent itself, not a human"),
    "judge": ("high", "evaluates the work of smaller models; a judge on the same "
              "tier as the judged adds nothing"),
    "fallback": ("high", "only invoked after the lower tier already failed"),
    "conversational": ("mid", "tone and coherence outweigh price when the reader "
                       "is your client's client"),
}

# The task class says what the work demands regardless of position. Higher of the
# two wins: an operational worker that happens to do tool using agentic work does
# not become cheap just by sitting at the bottom of the hierarchy.
TASK_TIERS = {
    "classification": ("cheap", "short semantic decision over short text"),
    "extraction": ("cheap", "find and copy, no synthesis"),
    "history_compression": ("cheap", "the consumer is the next loop iteration"),
    "summarization_for_humans": ("mid", "fidelity matters; the cheap tier invents"),
    "rag_answer": ("mid", "grounded synthesis over retrieved context"),
    "tool_using_agent": ("high", "decides which tool to call and what to do with "
                         "the result across many steps; an error at step 3 "
                         "contaminates everything after it"),
    "code_generation": ("high", "long dependency chains, expensive to verify"),
    "evaluation": ("high", "must be at least as capable as what it evaluates"),
}


def _max_tier(a: str, b: str) -> str:
    return a if TIER_ORDER.index(a) >= TIER_ORDER.index(b) else b


def resolve_tier(role: str, task_class: str | None = None) -> dict:
    """Required tier for a component, by the higher of the two rule."""
    if role not in ROLE_TIERS:
        raise ValueError(
            "unknown role %r. Valid roles: %s"
            % (role, ", ".join(sorted(ROLE_TIERS)))
        )
    role_tier, role_why = ROLE_TIERS[role]
    if not task_class:
        return {"tier": role_tier, "source": "role", "why": role_why}
    if task_class not in TASK_TIERS:
        raise ValueError(
            "unknown task_class %r. Valid classes: %s"
            % (task_class, ", ".join(sorted(TASK_TIERS)))
        )
    task_tier, task_why = TASK_TIERS[task_class]
    winner = _max_tier(role_tier, task_tier)
    source = "task" if winner == task_tier and task_tier != role_tier else "role"
    return {
        "tier": winner,
        "source": source,
        "why": task_why if source == "task" else role_why,
    }


# ---------------------------------------------------------------------------
# 2. Context inflation
# ---------------------------------------------------------------------------
def context_inflation(base: float, delta: float, iterations: int) -> dict:
    """Total input billed by a loop that resends its whole history each cycle.

        total = N x base + delta x N x (N - 1) / 2

    The quadratic term is the one nobody budgets for. Doubling the ceiling more
    than doubles the input cost, which is why max_iterations is an architecture
    decision with a price and not a tuning knob.
    """
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    per_iter = [base + delta * i for i in range(iterations)]
    total = sum(per_iter)
    naive = base * iterations
    return {
        "iterations": iterations,
        "base_tokens": base,
        "delta_per_iteration": delta,
        "input_per_iteration": per_iter,
        "input_last_iteration": per_iter[-1],
        "input_total": total,
        "input_total_naive": naive,
        "overspend_pct": round((total / naive - 1) * 100, 4) if naive else 0.0,
        "quadratic_share_pct": round((total - naive) / total * 100, 2) if total else 0.0,
    }


def loop_trace(base: float, delta: float, iterations: int, width: int = 44) -> str:
    """What the agent actually reads on each turn, drawn to scale.

    Written for the foundational session. Nobody disbelieves the formula, they
    disbelieve that it applies to their agent. A picture of the same loop they
    just wrote is harder to argue with than an equation.
    """
    per = [base + delta * i for i in range(iterations)]
    biggest = per[-1]
    running = 0.0
    lines = ["  turn   reads    bar" + " " * (width - 3) + "billed so far"]
    for i, tokens in enumerate(per, start=1):
        running += tokens
        filled = max(1, round(width * tokens / biggest))
        lines.append("  %4d  %6s   %s%s  %s"
                     % (i, f"{int(tokens):,}", "#" * filled,
                        "." * (width - filled), f"{int(running):,}"))
    naive = base * iterations
    lines.append("")
    lines.append("  the first turn is the only one that costs what you estimated.")
    lines.append("  billed %s against the %s you would have budgeted (%+.1f%%)."
                 % (f"{int(running):,}", f"{int(naive):,}",
                    (running / naive - 1) * 100 if naive else 0.0))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Map-reduce and semantic compression
# ---------------------------------------------------------------------------
def map_reduce(doc_tokens: float, chunks: int, prices: dict,
               map_model: str, synth_model: str,
               naive_model: str | None = None,
               map_output_tokens: float = 200,
               synth_input_tokens: float | None = None,
               synth_output_tokens: float = 500) -> dict:
    """Read a long document in parallel pieces, then reason over the digest.

    Naive: push the whole document at one capable model. Optimized: split it,
    let a cheap model copy out the relevant spans (Map), consolidate them in
    CODE (Reduce), and let the capable model reason over the digest.

    The Reduce phase has to be code. Consolidating with another model call is
    where teams accidentally rebuild the cost they were removing.

    The cost saving is real but it is not the headline. The headline is that a
    smaller, focused window removes the lost in the middle failure, so accuracy
    goes UP while the bill goes down. Accuracy is measured on a workload, never
    computed here, so this function does not pretend to return it.
    """
    naive_model = naive_model or synth_model
    for name in {map_model, synth_model, naive_model}:
        if name not in prices["models"]:
            raise ValueError("model %r is not in the price file" % name)
    if chunks < 1:
        raise ValueError("chunks must be >= 1")

    nm = prices["models"][naive_model]
    mm = prices["models"][map_model]
    sm = prices["models"][synth_model]

    naive_cost = (doc_tokens / 1000 * nm["input_per_1k"]
                  + synth_output_tokens / 1000 * nm["output_per_1k"])

    map_cost = (doc_tokens / 1000 * mm["input_per_1k"]
                + chunks * map_output_tokens / 1000 * mm["output_per_1k"])
    digest = (synth_input_tokens if synth_input_tokens is not None
              else chunks * map_output_tokens)
    synth_cost = (digest / 1000 * sm["input_per_1k"]
                  + synth_output_tokens / 1000 * sm["output_per_1k"])
    optimized = map_cost + synth_cost

    return {
        "naive_model": naive_model,
        "naive_window_tokens": doc_tokens,
        "naive_cost": round(naive_cost, 6),
        "map_model": map_model,
        "chunks": chunks,
        "chunk_tokens": round(doc_tokens / chunks, 1),
        "map_cost": round(map_cost, 6),
        "reduce_cost": 0.0,
        "reduce_note": "Reduce is CODE. Another model call here rebuilds the "
                       "cost you just removed.",
        "synth_model": synth_model,
        "synth_window_tokens": digest,
        "synth_cost": round(synth_cost, 6),
        "optimized_cost": round(optimized, 6),
        "saving_pct": round((1 - optimized / naive_cost) * 100, 2) if naive_cost else 0.0,
        "window_compression_pct": round((1 - digest / doc_tokens) * 100, 2),
        "accuracy_note": "accuracy is measured on a workload, not computed here",
    }


# ---------------------------------------------------------------------------
# 4. Cascade routing
# ---------------------------------------------------------------------------
def cascade(cost_cheap: float, cost_expensive: float, success_rate: float,
            tasks: int) -> dict:
    """Everything hits the cheap model first; only failures escalate.

        cost = T x c1 + T x (1 - p) x c2

    This beats going straight to the expensive model exactly when p > c1 / c2.
    What decides it is the price ratio of the two models actually chosen, never a
    fixed success percentage repeated from a blog post.

    Non negotiable condition: the cheap model's answer has to be validatable by
    CODE (schema, linter, explicit admission of failure). Validating with another
    model hands back the cost you were trying to avoid.
    """
    if not 0.0 <= success_rate <= 1.0:
        raise ValueError("success_rate must be between 0 and 1")
    if cost_expensive <= 0:
        raise ValueError("cost_expensive must be > 0")
    cascade_cost = tasks * cost_cheap + tasks * (1 - success_rate) * cost_expensive
    direct_cost = tasks * cost_expensive
    breakeven = cost_cheap / cost_expensive
    return {
        "cascade_cost": round(cascade_cost, 4),
        "direct_cost": round(direct_cost, 4),
        "saving": round(direct_cost - cascade_cost, 4),
        "saving_pct": round((1 - cascade_cost / direct_cost) * 100, 4),
        "min_success_rate": round(breakeven, 6),
        "worth_it": success_rate > breakeven,
        "condition": "cheap tier output must be validatable by deterministic code",
    }


# ---------------------------------------------------------------------------
# 4. Pricing
# ---------------------------------------------------------------------------
def load_prices(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for name, m in data.get("models", {}).items():
        for field in ("tier", "input_per_1k", "output_per_1k"):
            if field not in m:
                raise ValueError("model %r is missing %r in %s" % (name, field, path))
    return data


def _input_cost(input_tokens: float, model: dict, caching: bool,
                hit_rate: float, calls_per_write: int,
                prefix_tokens_per_call: float | None) -> tuple:
    """Input cost with caching modeled whole, including the two holes that both
    push the estimate DOWN when left out.

    1. Cache WRITE is billed. Writing the prefix costs more than reading input
       (1.25x on a 5 minute TTL), so caching is not pure saving. The write is
       amortized over the reads it serves before the TTL expires, and that read
       count is a declared premise, not a fact.
    2. The cacheable FLOOR is real and it is not monotonic across generations.
       Below the floor the cache simply does not happen, and the failure mode is
       SILENT in the API itself: no error, just zero cache creation tokens.
       Applying a hit rate to a short prompt is fiction.

    Returns (cost, alerts, effective_hit_rate). The third value exists because
    reporting the REQUESTED hit rate when the cache did not happen is the same
    silent lie this module is built to prevent.
    """
    alerts = []
    full = input_tokens / 1000 * model["input_per_1k"]
    if not caching or hit_rate <= 0:
        return full, alerts, 0.0

    floor = model.get("min_cache_tokens")
    per_call = prefix_tokens_per_call if prefix_tokens_per_call is not None else input_tokens
    if floor and per_call and per_call < floor:
        alerts.append(
            "caching OFF: the prefix is about %d tokens per call and this model's "
            "cacheable floor is %d. Below the floor the API does not cache and "
            "does not warn; billing as if it cached would understate the cost."
            % (round(per_call), floor))
        return full, alerts, 0.0

    read_rate = model.get("cache_read_per_1k")
    if read_rate is None:
        alerts.append("caching requested but this model declares no cache read "
                      "price; charged at full input price instead of assuming one")
        return full, alerts, 0.0

    cached = input_tokens * hit_rate
    fresh = input_tokens * (1 - hit_rate)
    cost = fresh / 1000 * model["input_per_1k"] + cached / 1000 * read_rate
    write_rate = model.get("cache_write_per_1k")
    if write_rate and calls_per_write > 0:
        cost += cached / 1000 * write_rate / calls_per_write
    return cost, alerts, hit_rate


def pick_model(tier: str, prices: dict) -> str:
    """Cheapest model that satisfies the required tier, by output price."""
    candidates = [(n, m) for n, m in prices["models"].items() if m["tier"] == tier]
    if not candidates:
        raise ValueError("no model on tier %r in the price file" % tier)
    candidates.sort(key=lambda kv: kv[1]["output_per_1k"])
    return candidates[0][0]


def price_component(comp: dict, prices: dict, opts: dict) -> dict:
    name = comp.get("name")
    if not name:
        raise ValueError("every component needs a name")

    role = comp.get("role")
    if not role:
        raise ValueError("component %r has no role" % name)
    req = resolve_tier(role, comp.get("task_class"))

    volume = comp.get("monthly_volume")
    if volume is None:
        # Refusing here is the whole point. A zeroed line vanishes from the
        # proposal and its absence is invisible.
        raise ValueError(
            "component %r has no monthly_volume. Without it this line would "
            "enter the estimate at zero and disappear from the proposal."
            % name)

    alerts = []
    in_loop = comp.get("max_iterations") is not None or comp.get("prompt_base_tokens") is not None
    if in_loop:
        ceiling = comp.get("max_iterations")
        if ceiling is None:
            raise ValueError(
                "agentic component %r has no max_iterations. Without a ceiling "
                "there is no estimate, only an open range." % name)
        base = comp.get("prompt_base_tokens")
        if base is None:
            raise ValueError(
                "agentic component %r has no prompt_base_tokens" % name)
        delta = comp.get("delta_tokens_per_iteration", 0)
        infl = context_inflation(base, delta, ceiling)
        input_per_task = infl["input_total"]
        output_per_task = comp.get("output_tokens_per_iteration", 0) * ceiling
        if not 5 <= ceiling <= 10:
            alerts.append(
                "iteration ceiling %d is outside the usual 5 to 10 band; %.1f%% "
                "of this component's input is the quadratic term"
                % (ceiling, infl["quadratic_share_pct"]))
        if not comp.get("caching"):
            alerts.append(
                "loop without caching: all %d cycles pay full input price, and "
                "the static prefix is reread every one of them" % ceiling)
        prefix = comp.get("static_prefix_tokens")
        # The cache fraction is DERIVED, not assumed: the static prefix reread
        # every cycle over the inflated input. Using a global hit rate here would
        # trade a known number for a premise.
        hit_rate = (prefix * ceiling / input_per_task
                    if comp.get("caching") and prefix and input_per_task else 0.0)
        prefix_per_call = prefix
    else:
        input_per_task = comp.get("input_tokens")
        output_per_task = comp.get("output_tokens", 0)
        if input_per_task is None:
            raise ValueError("component %r has no input_tokens" % name)
        hit_rate = opts["hit_rate"] if comp.get("caching", True) else 0.0
        prefix_per_call = input_per_task
        infl = {"single_call": True}

    model_name = comp.get("model") or pick_model(req["tier"], prices)
    model = prices["models"].get(model_name)
    if model is None:
        raise ValueError("model %r is not in the price file" % model_name)

    # The adequacy lock has to guard the SELECTED model, not only the swap
    # alternatives. Guarding only the alternatives lets a tool using agent on the
    # cheap tier pass silently with a low and wrong cost.
    if TIER_ORDER.index(model["tier"]) < TIER_ORDER.index(req["tier"]):
        alerts.append(
            "UNDERSIZED: %s is tier %s but this component requires %s (%s). "
            "Substitute: %s"
            % (model_name, model["tier"], req["tier"], req["why"],
               pick_model(req["tier"], prices)))
    elif TIER_ORDER.index(model["tier"]) > TIER_ORDER.index(req["tier"]):
        alerts.append(
            "OVERSIZED: %s is tier %s where tier %s suffices. Substitute: %s"
            % (model_name, model["tier"], req["tier"],
               pick_model(req["tier"], prices)))

    input_month = input_per_task * volume
    output_month = output_per_task * volume
    cost_in, cache_alerts, effective_hit_rate = _input_cost(
        input_month, model, bool(comp.get("caching", not in_loop)),
        hit_rate, opts["calls_per_write"], prefix_per_call)
    alerts.extend(cache_alerts)
    cost_out = output_month / 1000 * model["output_per_1k"]

    return {
        "name": name,
        "role": role,
        "model": model_name,
        "required_tier": req["tier"],
        "tier_source": req["source"],
        "tier_reason": req["why"],
        "monthly_calls": volume,
        "input_per_task": input_per_task,
        "input_per_task_naive": (infl.get("input_total_naive")
                                 if not infl.get("single_call") else input_per_task),
        "output_per_task": output_per_task,
        "input_tokens_month": input_month,
        "output_tokens_month": output_month,
        "cost_input_usd": round(cost_in, 2),
        "cost_output_usd": round(cost_out, 2),
        "cost_usd": round(cost_in + cost_out, 2),
        "cache_hit_rate_requested": round(hit_rate, 4),
        "cache_hit_rate": round(effective_hit_rate, 4),
        "inflation": infl,
        "alerts": alerts,
    }


PATTERNS = {
    "single_agent": ["specialist"],
    "router": ["router", "specialist"],
    "supervisor_specialist": ["supervisor", "specialist"],
    "hierarchical": ["director", "manager", "operational"],
    "map_reduce": ["extractor"],
    "cascade": ["operational", "fallback"],
}


def price_architecture(arch: dict, prices: dict, opts: dict | None = None) -> dict:
    opts = {"hit_rate": 0.40, "calls_per_write": 50, **(opts or {})}

    pattern = arch.get("orchestration_pattern")
    if pattern not in PATTERNS:
        raise ValueError(
            "unknown or missing orchestration_pattern %r. Valid: %s"
            % (pattern, ", ".join(sorted(PATTERNS))))

    components = arch.get("components") or []
    if not components:
        raise ValueError("architecture has no components")

    priced = [price_component(c, prices, opts) for c in components]
    total = sum(p["cost_usd"] for p in priced)
    roles = {p["role"] for p in priced}
    arch_alerts = []
    missing = [r for r in PATTERNS[pattern] if r not in roles]
    if missing:
        arch_alerts.append(
            "pattern %r expects roles %s and the architecture declares none of "
            "%s; a divergence here almost always means a component is missing "
            "from the estimate" % (pattern, PATTERNS[pattern], missing))

    for p in priced:
        p["cost_share_pct"] = round(100 * p["cost_usd"] / total, 1) if total else 0.0

    return {
        "project": arch.get("project", "unnamed"),
        "orchestration_pattern": pattern,
        "components": priced,
        "monthly_cost_usd": round(total, 2),
        "monthly_tokens": int(sum(p["input_tokens_month"] + p["output_tokens_month"]
                                  for p in priced)),
        "architecture_alerts": arch_alerts,
        "pricing_provenance": prices.get("provenance", "undeclared"),
        "pricing_as_of": prices.get("pricing_as_of", "undeclared"),
        "premises": [
            "cache write amortized over %d reads (declared premise, not a fact)"
            % opts["calls_per_write"],
            "cache hit rate %.0f%% for single call components (derived, not "
            "assumed, for loop components)" % (opts["hit_rate"] * 100),
            "LLM vector only: infrastructure and third party APIs are separate "
            "vectors and are deliberately out of scope",
        ],
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_table(result: dict) -> str:
    rows = [("component", "calls/mo", "in/task", "model", "USD/mo", "%")]
    for c in result["components"]:
        rows.append((c["name"][:30], f"{c['monthly_calls']:,}",
                     f"{int(c['input_per_task']):,}", c["model"],
                     f"{c['cost_usd']:,.2f}", f"{c['cost_share_pct']:.1f}%"))
    widths = [max(len(r[i]) for r in rows) for i in range(6)]
    out = []
    for n, r in enumerate(rows):
        out.append("  ".join(
            r[i].ljust(widths[i]) if i in (0, 3) else r[i].rjust(widths[i])
            for i in range(6)))
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    out.append("  ".join("-" * w for w in widths))
    out.append("TOTAL".ljust(widths[0]) + "  " +
               " " * (widths[1] + widths[2] + widths[3] + 6) +
               f"{result['monthly_cost_usd']:,.2f}".rjust(widths[4]))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Self test: every number quoted on stage is pinned here
# ---------------------------------------------------------------------------
def self_test() -> int:
    n = 0

    # Context inflation, the reference case from the talk.
    r = context_inflation(2000, 500, 10)
    assert r["input_total"] == 42500, r["input_total"]; n += 1
    assert r["input_total_naive"] == 20000; n += 1
    assert r["overspend_pct"] == 112.5, r["overspend_pct"]; n += 1
    assert r["input_per_iteration"][0] == 2000; n += 1
    assert r["input_per_iteration"][-1] == 6500; n += 1
    # Doubling the ceiling more than doubles the input: the headline consequence.
    r20 = context_inflation(2000, 500, 20)
    assert r20["input_total"] == 135000; n += 1
    assert r20["input_total"] > 2 * r["input_total"]; n += 1
    # The specialist in the example architecture.
    r8 = context_inflation(6000, 500, 8)
    assert r8["input_total"] == 62000, r8["input_total"]; n += 1
    assert r8["input_total_naive"] == 48000; n += 1

    # Cascade: the break even is the price ratio, never a fixed percentage.
    c = cascade(1, 5, 0.60, 100)
    assert c["cascade_cost"] == 300.0; n += 1
    assert c["direct_cost"] == 500.0; n += 1
    assert c["saving_pct"] == 40.0; n += 1
    assert c["min_success_rate"] == 0.2; n += 1
    assert c["worth_it"] is True; n += 1
    c80 = cascade(1, 5, 0.80, 100)
    assert c80["saving_pct"] == 60.0; n += 1
    # Below the break even the same architecture costs MORE. This is the slide
    # that stops people quoting cascade as a free win.
    c02 = cascade(1, 5, 0.02, 100)
    assert c02["cascade_cost"] == 590.0; n += 1
    assert round(c02["saving_pct"], 1) == -18.0, c02["saving_pct"]; n += 1
    assert c02["worth_it"] is False; n += 1
    # Same success rate, different price ratio, opposite verdict.
    assert cascade(1, 50, 0.05, 100)["worth_it"] is True; n += 1
    assert cascade(1, 2, 0.05, 100)["worth_it"] is False; n += 1

    # Loop trace: the drawing has to agree with the arithmetic.
    here_pins = os.path.dirname(os.path.abspath(__file__))
    trace = loop_trace(2000, 500, 10)
    assert "42,500" in trace, trace; n += 1
    assert "20,000" in trace; n += 1
    # header + 10 turns + blank + 2 closing lines
    assert len(trace.splitlines()) == 14, len(trace.splitlines()); n += 1

    # Map-reduce. The digest is what the capable model reads, and the saving
    # follows the tier ratio, same as cascade.
    prices_pins = load_prices(os.path.join(here_pins, "prices.example.json"))
    mr = map_reduce(40000, 10, prices_pins, "Claude Haiku 4.5", "Claude Sonnet 5")
    assert mr["naive_window_tokens"] == 40000; n += 1
    assert mr["synth_window_tokens"] == 2000; n += 1
    assert mr["window_compression_pct"] == 95.0, mr["window_compression_pct"]; n += 1
    assert mr["reduce_cost"] == 0.0; n += 1
    assert mr["optimized_cost"] < mr["naive_cost"]; n += 1
    # Pinned on purpose. This was 50.2% while the table carried Sonnet 5 at 3/15;
    # at the vendor's real 2/10 the reader is only twice the mapper's input price
    # and the same restructuring saves 30.6%. Nothing in the design changed, only
    # the price ratio, which is the whole point of section 4.
    assert mr["saving_pct"] == 30.59, mr["saving_pct"]; n += 1
    # Against a frontier model the same restructuring saves far more, because
    # what drives it is the price ratio between the tiers and nothing else.
    mr_high = map_reduce(40000, 10, prices_pins, "Claude Haiku 4.5",
                         "Claude Opus 5", naive_model="Claude Opus 5")
    assert mr_high["saving_pct"] > mr["saving_pct"], (mr_high["saving_pct"], mr["saving_pct"]); n += 1
    # And it refuses a model it cannot price.
    try:
        map_reduce(40000, 10, prices_pins, "Gemini 3", "Claude Sonnet 5")
        raise AssertionError("unknown map model must raise")
    except ValueError as e:
        assert "not in the price file" in str(e); n += 1

    # Higher of the two rule.
    assert resolve_tier("operational")["tier"] == "cheap"; n += 1
    t = resolve_tier("operational", "tool_using_agent")
    assert t["tier"] == "high" and t["source"] == "task"; n += 1
    t = resolve_tier("judge", "classification")
    assert t["tier"] == "high" and t["source"] == "role"; n += 1
    # Compression for the agent is cheap; summarization a human reads is not.
    assert resolve_tier("compressor", "history_compression")["tier"] == "cheap"; n += 1
    assert resolve_tier("compressor", "summarization_for_humans")["tier"] == "mid"; n += 1
    assert len(ROLE_TIERS) == 11; n += 1
    try:
        resolve_tier("architect")
        raise AssertionError("unknown role must raise")
    except ValueError as e:
        assert "Valid roles" in str(e); n += 1

    # End to end on the example architecture, pinned to the cent.
    here = os.path.dirname(os.path.abspath(__file__))
    prices = load_prices(os.path.join(here, "prices.example.json"))
    with open(os.path.join(here, "architecture.example.json"), encoding="utf-8") as fh:
        arch = json.load(fh)
    res = price_architecture(arch, prices)
    by_name = {c["name"]: c for c in res["components"]}
    assert by_name["Intent triage"]["cost_usd"] == 40.00, by_name["Intent triage"]["cost_usd"]; n += 1
    assert by_name["Claim specialist"]["cost_usd"] == 1440.00, by_name["Claim specialist"]["cost_usd"]; n += 1
    assert by_name["Document extractor"]["cost_usd"] == 73.80; n += 1
    assert by_name["History compressor"]["cost_usd"] == 106.20; n += 1
    assert by_name["Final decision review"]["cost_usd"] == 25.88; n += 1
    assert res["monthly_cost_usd"] == 1685.88, res["monthly_cost_usd"]; n += 1

    # The headline of the talk: fewer calls, far more cost.
    triage, spec = by_name["Intent triage"], by_name["Claim specialist"]
    assert triage["monthly_calls"] > 4 * spec["monthly_calls"]; n += 1
    assert spec["cost_usd"] > 30 * triage["cost_usd"]; n += 1
    assert spec["cost_share_pct"] == 85.4, spec["cost_share_pct"]; n += 1
    assert triage["cost_share_pct"] == 2.4; n += 1

    # Caching fails SILENTLY below the floor, so the estimator must say so.
    assert any("cacheable floor" in a for a in triage["alerts"]), triage["alerts"]; n += 1
    # The REQUESTED rate was 40% and the EFFECTIVE rate is zero. Reporting the
    # requested one was a real defect in this file, caught by this assert: an
    # estimator that says 40% cached while billing 0% cached is the same silent
    # lie the module exists to prevent.
    assert triage["cache_hit_rate_requested"] == 0.40; n += 1
    assert triage["cache_hit_rate"] == 0.0; n += 1
    assert triage["cost_input_usd"] == 36.00, triage["cost_input_usd"]; n += 1
    # And the loop's cache fraction is derived, not assumed.
    assert round(spec["cache_hit_rate"], 4) == round(48000 / 62000, 4); n += 1

    # The three refusals.
    def refuses(mutate, needle):
        broken = json.loads(json.dumps(arch))
        mutate(broken)
        try:
            price_architecture(broken, prices)
        except ValueError as e:
            assert needle in str(e), "%r not in %r" % (needle, str(e))
            return True
        raise AssertionError("expected refusal for: " + needle)

    assert refuses(lambda a: a["components"][1].pop("max_iterations"),
                   "no max_iterations"); n += 1
    assert refuses(lambda a: a["components"][0].pop("monthly_volume"),
                   "no monthly_volume"); n += 1
    assert refuses(lambda a: a.__setitem__("orchestration_pattern", "swarm"),
                   "unknown or missing orchestration_pattern"); n += 1
    assert refuses(lambda a: a.__setitem__("components", []),
                   "no components"); n += 1

    # The adequacy lock must guard the SELECTED model, not only the alternatives.
    downgraded = json.loads(json.dumps(arch))
    downgraded["components"][1]["model"] = "Claude Haiku 4.5"
    res2 = price_architecture(downgraded, prices)
    spec2 = [c for c in res2["components"] if c["name"] == "Claim specialist"][0]
    assert any(a.startswith("UNDERSIZED") for a in spec2["alerts"]), spec2["alerts"]; n += 1
    # And the wrong model makes the bill look BETTER, which is the trap.
    assert res2["monthly_cost_usd"] < res["monthly_cost_usd"]; n += 1

    print("self-test OK (%d asserts)" % n)
    return 0


# ---------------------------------------------------------------------------
def _prices_for_run(a) -> dict:
    """The hand table by default, the vendors' sources with --live.

    The network module is imported only when asked for, so everything else in
    this file keeps running with no network at all."""
    here = os.path.dirname(os.path.abspath(__file__))
    base_path = a.prices or os.path.join(here, "prices.example.json")
    if not a.live:
        return load_prices(base_path)
    if here not in sys.path:          # also works under `python -I` and from any cwd
        sys.path.insert(0, here)
    import live_prices
    with open(base_path, encoding="utf-8") as fh:
        base = json.load(fh)
    prices, notes = live_prices.load_with_fallback(
        base, os.path.join(here, "prices.live.json"))
    for note in notes:
        print("NOTE  " + note, file=sys.stderr)
    drift = live_prices.diff(base, prices)
    if drift:
        print("the hand table disagrees with the live source on %d field(s); "
              "pricing from the source:" % len(drift), file=sys.stderr)
        for name, field, mine, theirs in drift:
            print("  %-18s %-20s table %-10s source %s" % (name, field, mine, theirs),
                  file=sys.stderr)
        print(file=sys.stderr)
    return prices


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Price an agentic architecture before you build it.")
    p.add_argument("--architecture", help="JSON with orchestration_pattern and components")
    p.add_argument("--prices", default=None, help="price file (default: prices.example.json)")
    p.add_argument("--live", action="store_true",
                   help="price from the vendors' public sources right now (needs network)")
    p.add_argument("--inflation", nargs=3, metavar=("BASE", "DELTA", "N"), type=float)
    p.add_argument("--cascade", nargs=4, metavar=("C_CHEAP", "C_EXPENSIVE", "RATE", "TASKS"), type=float)
    p.add_argument("--trace", nargs=3, metavar=("BASE", "DELTA", "N"), type=float,
                   help="draw what the agent reads on each turn, to scale")
    p.add_argument("--mapreduce", nargs=2, metavar=("DOC_TOKENS", "CHUNKS"), type=float,
                   help="naive single window against map, reduce in code, synthesize")
    p.add_argument("--map-model", default="Claude Haiku 4.5")
    p.add_argument("--synth-model", default="Claude Sonnet 5")
    p.add_argument("--naive-model", default=None)
    p.add_argument("--roles", action="store_true", help="list roles and required tiers")
    p.add_argument("--json", action="store_true", help="raw JSON instead of a table")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)

    if a.self_test:
        return self_test()

    if a.roles:
        print(f"{'role':<16}{'tier':<8}why")
        for role, (tier, why) in ROLE_TIERS.items():
            print(f"{role:<16}{tier:<8}{why}")
        print()
        print(f"{'task class':<28}{'tier':<8}why")
        for k, (tier, why) in TASK_TIERS.items():
            print(f"{k:<28}{tier:<8}{why}")
        return 0

    if a.inflation:
        base, delta, n = a.inflation
        r = context_inflation(base, delta, int(n))
        print(json.dumps(r, indent=2))
        print("\n%d steps bill %s tokens where the naive count says %s: %+.1f%%"
              % (r["iterations"], f"{int(r['input_total']):,}",
                 f"{int(r['input_total_naive']):,}", r["overspend_pct"]))
        return 0

    if a.trace:
        base, delta, n = a.trace
        print(loop_trace(base, delta, int(n)))
        return 0

    if a.mapreduce:
        prices = _prices_for_run(a)
        doc, chunks = a.mapreduce
        r = map_reduce(doc, int(chunks), prices, a.map_model, a.synth_model,
                       naive_model=a.naive_model)
        print(json.dumps(r, indent=2))
        print("\n%s reads %s tokens at %.4f. Map on %s plus a code Reduce leaves "
              "%s tokens for %s: %.4f, which is %.1f%% cheaper on a window %.1f%% "
              "smaller.\nAccuracy is the real prize and it is measured, not "
              "computed: a focused window removes the lost in the middle failure."
              % (r["naive_model"], f"{int(r['naive_window_tokens']):,}",
                 r["naive_cost"], r["map_model"],
                 f"{int(r['synth_window_tokens']):,}", r["synth_model"],
                 r["optimized_cost"], r["saving_pct"], r["window_compression_pct"]))
        return 0

    if a.cascade:
        c1, c2, rate, tasks = a.cascade
        r = cascade(c1, c2, rate, int(tasks))
        print(json.dumps(r, indent=2))
        verdict = "worth it" if r["worth_it"] else "MORE EXPENSIVE than going direct"
        print("\ncascade %.2f vs direct %.2f (%+.1f%%): %s. Break even at %.1f%% "
              "success, which is c1/c2 and nothing else."
              % (r["cascade_cost"], r["direct_cost"], r["saving_pct"], verdict,
                 r["min_success_rate"] * 100))
        return 0

    if a.architecture:
        prices = _prices_for_run(a)
        with open(a.architecture, encoding="utf-8") as fh:
            arch = json.load(fh)
        res = price_architecture(arch, prices)
        if a.json:
            print(json.dumps(res, indent=2))
            return 0
        print(res["project"])
        print("pattern: %s | prices: %s (%s)"
              % (res["orchestration_pattern"], res["pricing_provenance"],
                 res["pricing_as_of"]))
        print()
        print(render_table(res))
        print()
        for c in res["components"]:
            if c.get("input_per_task_naive") and c["input_per_task"] != c["input_per_task_naive"]:
                print("  %s: %s input tokens per task against a naive %s (%d iterations)"
                      % (c["name"], f"{int(c['input_per_task']):,}",
                         f"{int(c['input_per_task_naive']):,}",
                         c["inflation"]["iterations"]))
        alerts = [(c["name"], al) for c in res["components"] for al in c["alerts"]]
        if alerts:
            print("\nALERTS")
            for name, al in alerts:
                print("  [%s] %s" % (name, al))
        for al in res["architecture_alerts"]:
            print("  [architecture] %s" % al)
        print("\nPREMISES")
        for pr in res["premises"]:
            print("  " + pr)
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
