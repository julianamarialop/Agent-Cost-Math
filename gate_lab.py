# -*- coding: utf-8 -*-
"""Four ways a data quality gate can be green and dead, and the harness that
catches all four.

Companion code for the expert session "The Gate That Was Never On".

A gate is a claim about your pipeline. A green gate that never executed is a
claim you have stopped checking, and it is worse than no gate at all, because
now everyone believes the check exists.

Every failure mode below is one I shipped and then found, all of them in a suite
whose rules were written down, reviewed and believed:

  NEVER_RAN     the rule read a column that does not exist, so it errored,
                fell through to the permitted default, and reported nothing
  ORPHAN        the check existed, the item was removed from the manifest, and
                the check became unreachable while still passing
  FALSE_POSITIVE the rule failed correct data, so the team learned to skip it
  CRASHES       the rule raised, and a crash does not teach "fix the data",
                it teaches "skip the step"

The only defense that works is a mutation harness: prove each rule fails the
broken input AND passes the correct one. A rule tested in one direction only is
a rule you have not tested.

    python gate_lab.py
    python gate_lab.py --mutation
    python gate_lab.py --self-test
"""
from __future__ import annotations

import argparse
import json

# A tiny batch of records, as a data gate would receive it.
GOOD = [
    {"customer_id": "C-001", "amount": 120.50, "currency": "BRL",
     "status": "active", "notes": "cliente todo regularizado"},
    {"customer_id": "C-002", "amount": 88.00, "currency": "BRL",
     "status": "churned", "notes": "encerrou em marco"},
    {"customer_id": "C-003", "amount": 4210.75, "currency": "USD",
     "status": "active", "notes": "todos os contratos revisados"},
]

BROKEN = [
    {"customer_id": "C-001", "amount": 120.50, "currency": "BRL",
     "status": "active", "notes": "ok"},
    {"customer_id": "C-001", "amount": -5.00, "currency": "XXX",
     "status": "ativo", "notes": "TBD"},
]

VALID_STATUS = {"active", "churned", "suspended"}
VALID_CURRENCY = {"BRL", "USD", "EUR", "GBP"}


# ---------------------------------------------------------------------------
# The rules, in two versions each: the one that looks right and the one that is.
# ---------------------------------------------------------------------------
def rule_unique_id_broken(rows: list) -> list:
    """NEVER_RAN. It reads 'id', and the column is 'customer_id'.

    The typo alone would be loud. What makes it SILENT is the `if key` guard,
    written for the entirely reasonable purpose of not validating empty values:
    the field name is wrong, so key is always None, so the guard is always false
    and the rule reports clean forever. Two defensible decisions combine into a
    check that cannot fail.

    This is the shape of the worst defect I have shipped: a lock that pointed at
    a sheet no generation of the file ever had, so it always failed, always fell
    through to the calculation it existed to forbid, and never said a word.
    """
    seen, dupes = set(), []
    for r in rows:
        key = r.get("id")
        if key and key in seen:
            dupes.append(key)
        if key:
            seen.add(key)
    return ["duplicate id: %s" % d for d in dupes]


def rule_unique_id(rows: list) -> list:
    """The same rule, refusing to run on a field it cannot find."""
    findings = []
    seen = set()
    for i, r in enumerate(rows):
        if "customer_id" not in r:
            # Refuse rather than evaluate an absent field. A rule that cannot
            # find its input has not passed, it has not run.
            return ["row %d has no customer_id: the rule cannot evaluate and "
                    "will not report clean" % i]
        key = r["customer_id"]
        if key in seen:
            findings.append("duplicate customer_id: %s" % key)
        seen.add(key)
    return findings


def rule_placeholder_broken(rows: list) -> list:
    """FALSE_POSITIVE. Case insensitive 'todo' in Portuguese text.

    'TODO' is a placeholder marker in English. 'todo' is an extremely common
    Portuguese word meaning 'all'. Matching case insensitively rejected almost
    every correct Portuguese document in a suite of mine, and a gate that fails
    correct work is a gate the team learns to switch off.
    """
    out = []
    for r in rows:
        text = (r.get("notes") or "").lower()
        for marker in ("tbd", "todo", "xxx"):
            if marker in text:
                out.append("placeholder %r in %s" % (marker, r["customer_id"]))
    return out


def rule_placeholder(rows: list) -> list:
    """Case SENSITIVE, whole word. The markers are written in caps by convention,
    and that convention is what makes them detectable without false positives."""
    import re
    out = []
    for r in rows:
        text = r.get("notes") or ""
        for marker in ("TBD", "TODO", "XXX"):
            if re.search(r"(?<![A-Za-z])" + marker + r"(?![A-Za-z])", text):
                out.append("placeholder %r in %s" % (marker, r["customer_id"]))
    return out


def rule_amount(rows: list) -> list:
    return ["negative amount in %s" % r["customer_id"]
            for r in rows if r.get("amount", 0) < 0]


def rule_currency(rows: list) -> list:
    return ["invalid currency %r in %s" % (r.get("currency"), r["customer_id"])
            for r in rows if r.get("currency") not in VALID_CURRENCY]


def rule_status_crashes(rows: list) -> list:
    """CRASHES. Uppercases a value that can be None, and dies.

    The pipeline logs a traceback, the on call engineer reruns with the gate
    disabled, and the gate is now decoration. A crash does not teach "fix the
    data", it teaches "skip the step".
    """
    return [r["customer_id"] for r in rows
            if r["status"].upper() not in {s.upper() for s in VALID_STATUS}]


def rule_status(rows: list) -> list:
    """Same check, tolerant of the absent value, strict about the invalid one."""
    out = []
    for r in rows:
        val = r.get("status")
        if val is None:
            out.append("missing status in %s" % r.get("customer_id", "?"))
        elif val not in VALID_STATUS:
            out.append("invalid status %r in %s (valid: %s)"
                       % (val, r.get("customer_id", "?"),
                          ", ".join(sorted(VALID_STATUS))))
    return out


# ---------------------------------------------------------------------------
# The manifest, and the orphan problem
# ---------------------------------------------------------------------------
# ORPHAN: the gate runs the rules named in the manifest. Delete a name and the
# function stays in the file, still imported, still passing its own unit test,
# and never executed again. Nothing goes red. In my case two checks sat orphaned
# and inert for a whole release, and the only reason I found them was a run that
# reported which registered rules had no manifest entry.
MANIFEST = ["unique_id", "placeholder", "amount", "currency", "status"]

RULES = {
    "unique_id": rule_unique_id,
    "placeholder": rule_placeholder,
    "amount": rule_amount,
    "currency": rule_currency,
    "status": rule_status,
}

BROKEN_RULES = {
    "unique_id": rule_unique_id_broken,
    "placeholder": rule_placeholder_broken,
    "amount": rule_amount,
    "currency": rule_currency,
    "status": rule_status_crashes,
}


def run_gate(rows: list, rules: dict = None, manifest: list = None) -> dict:
    """Runs the gate and, just as importantly, reports its own coverage."""
    rules = rules if rules is not None else RULES
    manifest = manifest if manifest is not None else MANIFEST

    findings, errors, ran = {}, {}, []
    for name in manifest:
        fn = rules.get(name)
        if fn is None:
            errors[name] = "manifest names a rule that does not exist"
            continue
        try:
            out = fn(rows)
        except Exception as exc:
            # A rule that raised did not pass. Recording it as an ERROR rather
            # than swallowing it is the difference between a gate and a wish.
            errors[name] = "%s: %s" % (type(exc).__name__, exc)
            continue
        ran.append(name)
        if out:
            findings[name] = out

    orphans = sorted(set(rules) - set(manifest))
    return {
        "rows": len(rows),
        "rules_in_manifest": len(manifest),
        "rules_ran": ran,
        "findings": findings,
        "errors": errors,
        "orphan_rules": orphans,
        # A gate passes only if every rule RAN and found nothing. Green with an
        # error, or green with an orphan, is not green.
        "passed": not findings and not errors and not orphans,
        "verdict": ("PASS" if not findings and not errors and not orphans
                    else "FAIL"),
    }


# ---------------------------------------------------------------------------
# The mutation harness: the only thing that proves a gate is alive
# ---------------------------------------------------------------------------
MUTATIONS = [
    ("unique_id", "duplicate the first row's id",
     lambda rows: rows + [dict(rows[0])]),
    ("amount", "flip an amount negative",
     lambda rows: [{**rows[0], "amount": -1.0}] + rows[1:]),
    ("currency", "set an unknown currency",
     lambda rows: [{**rows[0], "currency": "XXX"}] + rows[1:]),
    ("status", "set an untranslated status",
     lambda rows: [{**rows[0], "status": "ativo"}] + rows[1:]),
    ("status", "drop the status entirely",
     lambda rows: [{k: v for k, v in rows[0].items() if k != "status"}] + rows[1:]),
    ("placeholder", "leave a real placeholder marker",
     lambda rows: [{**rows[0], "notes": "revisar TBD antes de publicar"}] + rows[1:]),
]


def mutation_report(rules: dict = None, manifest: list = None) -> dict:
    """Two directions, both required.

    Direction 1: correct data must PASS. A rule that fails correct data gets
    switched off by the team, so a false positive is a dead gate too.
    Direction 2: each mutation must be CAUGHT by its own rule. Caught by some
    other rule does not count, because then the first rule is untested.
    """
    rules = rules if rules is not None else RULES
    manifest = manifest if manifest is not None else MANIFEST

    clean = run_gate(GOOD, rules, manifest)
    results = []
    for rule_name, description, mutate in MUTATIONS:
        mutated = mutate([dict(r) for r in GOOD])
        res = run_gate(mutated, rules, manifest)
        caught_by = sorted(res["findings"])
        results.append({
            "rule": rule_name,
            "mutation": description,
            "caught": rule_name in res["findings"],
            "caught_by": caught_by,
            "errors": res["errors"],
        })

    survived = [r for r in results if not r["caught"]]
    return {
        "clean_data_passes": clean["passed"],
        "clean_data_findings": clean["findings"],
        "clean_data_errors": clean["errors"],
        "mutations": results,
        "surviving_mutations": survived,
        "score": "%d/%d" % (len(results) - len(survived), len(results)),
        "gate_is_alive": clean["passed"] and not survived,
    }


def self_test() -> int:
    n = 0

    # The correct gate: clean data passes, every mutation is caught by its own rule.
    rep = mutation_report()
    assert rep["clean_data_passes"] is True, rep["clean_data_findings"]; n += 1
    assert rep["surviving_mutations"] == [], rep["surviving_mutations"]; n += 1
    assert rep["gate_is_alive"] is True; n += 1
    assert rep["score"] == "6/6", rep["score"]; n += 1

    # NEVER_RAN: the broken uniqueness rule reads the wrong field, so a real
    # duplicate survives. The gate reports clean on data that is not.
    dup = GOOD + [dict(GOOD[0])]
    assert "unique_id" in run_gate(dup)["findings"]; n += 1
    assert "unique_id" not in run_gate(dup, BROKEN_RULES)["findings"]; n += 1
    # And it is not even an error, which is what makes it invisible.
    assert "unique_id" not in run_gate(dup, BROKEN_RULES)["errors"]; n += 1

    # FALSE_POSITIVE: the case insensitive placeholder rule fails CORRECT
    # Portuguese text, twice, on data that has no placeholder at all.
    bad_rep = mutation_report(BROKEN_RULES)
    assert bad_rep["clean_data_passes"] is False; n += 1
    assert "placeholder" in bad_rep["clean_data_findings"]; n += 1
    assert len(bad_rep["clean_data_findings"]["placeholder"]) == 2, \
        bad_rep["clean_data_findings"]["placeholder"]; n += 1
    # The correct rule finds nothing in the same text.
    assert rule_placeholder(GOOD) == []; n += 1
    # And still catches a real marker.
    assert rule_placeholder([{"customer_id": "C-9", "notes": "revisar TBD hoje"}]); n += 1
    # Whole word, so a Portuguese word containing a marker is not a hit.
    assert rule_placeholder([{"customer_id": "C-9", "notes": "TODOS os contratos"}]) == []; n += 1

    # CRASHES: the status rule dies on a missing key instead of reporting it.
    no_status = [{k: v for k, v in GOOD[0].items() if k != "status"}]
    assert "status" in run_gate(no_status, BROKEN_RULES)["errors"]; n += 1
    assert run_gate(no_status, BROKEN_RULES)["passed"] is False; n += 1
    # The correct rule reports it as a finding, which is actionable.
    assert "status" in run_gate(no_status)["findings"]; n += 1

    # ORPHAN: drop a rule from the manifest and it stops running, silently. The
    # data is dirty and the gate says nothing about it.
    thin = [r for r in MANIFEST if r != "currency"]
    bad_currency = [{**GOOD[0], "currency": "XXX"}]
    assert "currency" in run_gate(bad_currency)["findings"]; n += 1
    res_thin = run_gate(bad_currency, RULES, thin)
    assert "currency" not in res_thin["findings"]; n += 1
    # But the gate reports the orphan and refuses to call itself green.
    assert res_thin["orphan_rules"] == ["currency"]; n += 1
    assert res_thin["passed"] is False; n += 1
    # Which is the whole point: an unreachable check must break the build.
    clean_thin = run_gate(GOOD, RULES, thin)
    assert clean_thin["findings"] == {} and clean_thin["passed"] is False; n += 1

    # A manifest naming a rule that does not exist is an error, not a skip.
    ghost = run_gate(GOOD, RULES, MANIFEST + ["freshness"])
    assert "freshness" in ghost["errors"]; n += 1
    assert ghost["passed"] is False; n += 1

    # The crashing rule survives its own mutation: drop the status and the rule
    # raises instead of reporting, so the mutation is never caught by a check.
    st = [m for m in bad_rep["mutations"]
          if m["mutation"] == "drop the status entirely"][0]
    assert st["caught"] is False, st; n += 1
    assert "status" in st["errors"], st; n += 1
    assert "KeyError" in st["errors"]["status"], st["errors"]; n += 1

    # And coverage credited to the WRONG rule is not coverage. On the broken
    # gate the untranslated status mutation gets flagged by the placeholder
    # rule too, purely as a side effect of its false positives on Portuguese.
    st2 = [m for m in bad_rep["mutations"]
           if m["mutation"] == "set an untranslated status"][0]
    assert "placeholder" in st2["caught_by"], st2; n += 1
    assert len(st2["caught_by"]) > 1, st2; n += 1

    print("gate_lab self-test OK (%d asserts)" % n)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Four ways a data gate can be green and dead.")
    p.add_argument("--broken", action="store_true",
                   help="run the plausible looking gate instead of the correct one")
    p.add_argument("--mutation", action="store_true", help="mutation report")
    p.add_argument("--thin-manifest", action="store_true",
                   help="drop a rule from the manifest and watch it go orphan")
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()

    if a.self_test:
        raise SystemExit(self_test())

    rules = BROKEN_RULES if a.broken else RULES
    manifest = [r for r in MANIFEST if r != "currency"] if a.thin_manifest else MANIFEST
    label = "the plausible gate" if a.broken else "the correct gate"

    if a.mutation:
        rep = mutation_report(rules, manifest)
        if a.json:
            print(json.dumps(rep, indent=2))
            raise SystemExit(0)
        print("MUTATION REPORT, %s\n" % label)
        print("  direction 1: does correct data pass?  %s"
              % ("yes" % () if rep["clean_data_passes"] else "NO"))
        for rule, items in rep["clean_data_findings"].items():
            for it in items:
                print("      false positive [%s] %s" % (rule, it))
        for rule, err in rep["clean_data_errors"].items():
            print("      error [%s] %s" % (rule, err))
        print("\n  direction 2: is each mutation caught by its OWN rule?")
        for m in rep["mutations"]:
            mark = "caught" if m["caught"] else "SURVIVED"
            extra = ""
            if not m["caught"] and m["caught_by"]:
                extra = "  (only flagged by %s)" % ", ".join(m["caught_by"])
            if not m["caught"] and m["errors"]:
                extra = "  (rule errored: %s)" % list(m["errors"])[0]
            print("      %-9s %-18s %s%s" % (mark, m["rule"], m["mutation"], extra))
        print("\n  score %s   gate is alive: %s"
              % (rep["score"], rep["gate_is_alive"]))
        raise SystemExit(0 if rep["gate_is_alive"] else 1)

    for name, rows in (("CORRECT DATA", GOOD), ("DIRTY DATA", BROKEN)):
        res = run_gate(rows, rules, manifest)
        if a.json:
            print(json.dumps(res, indent=2))
            continue
        print("%s against %s: %s" % (label, name, res["verdict"]))
        print("  ran %d of %d rules: %s"
              % (len(res["rules_ran"]), res["rules_in_manifest"],
                 ", ".join(res["rules_ran"])))
        for rule, items in res["findings"].items():
            for it in items:
                print("  finding [%s] %s" % (rule, it))
        for rule, err in res["errors"].items():
            print("  ERROR   [%s] %s" % (rule, err))
        for orph in res["orphan_rules"]:
            print("  ORPHAN  [%s] registered but not in the manifest, so it "
                  "never runs" % orph)
        print()
