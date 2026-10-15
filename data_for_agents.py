# -*- coding: utf-8 -*-
"""Why an agent answers your business question wrong, and why it is not the model.

Companion code for the foundational session "Your Data Was Not Built for Agents".

A person opening a table reads the column names, notices the one called
CLI_STA_END looks suspicious, asks a colleague, and moves on. An agent cannot do
any of that. It has exactly what your catalog declares, and if the catalog
declares nothing, it guesses from the column name.

This file shows the same question answered against the same three tables, twice:
once with a raw schema and once with a semantic contract. Nothing about the model
changes between the two runs. Only the metadata does.

    python data_for_agents.py
    python data_for_agents.py --question revenue
    python data_for_agents.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re

# --- The warehouse, as it actually looks in most companies ------------------
# Three tables that all plausibly answer "how many active customers do we have".
# One of them is right. Nothing here says which.
RAW = {
    "DW.TB_CLI_001": {
        "columns": ["ID_CLI", "NM_CLI", "CLI_STA_END", "DT_CAD", "FL_ATV"],
        "rows": 4_812_339,
    },
    "STG.CLIENTE_LOG": {
        "columns": ["ID_CLI", "FL_ATIVO", "DT_EVT", "TP_EVT"],
        "rows": 71_004_112,
    },
    "MART.DIM_CUSTOMER": {
        "columns": ["customer_key", "customer_name", "is_active", "valid_from",
                    "valid_to", "tax_id"],
        "rows": 4_530_881,
    },
}

# --- The same warehouse, with a contract ------------------------------------
# Every field here answers a question the agent would otherwise have to guess:
# what does this mean, what is one row, is it trustworthy, and may I read it.
CONTRACT = {
    "DW.TB_CLI_001": {
        "description": "Raw customer landing table from the core system. Kept for "
                       "lineage. Superseded by MART.DIM_CUSTOMER.",
        "grain": "one row per customer per load, duplicates expected",
        "certified": False,
        "columns": {
            "ID_CLI": {"means": "customer identifier"},
            "NM_CLI": {"means": "customer name", "pii": True},
            "CLI_STA_END": {"means": "ADDRESS status, not customer status. "
                                     "Named before the address split in 2011."},
            "DT_CAD": {"means": "registration date"},
            "FL_ATV": {"means": "active flag as sent by the core system, not "
                                "reconciled against churn"},
        },
    },
    "STG.CLIENTE_LOG": {
        "description": "Event log of customer state changes. Staging layer.",
        "grain": "one row per event, NOT per customer",
        "certified": False,
        "columns": {
            "ID_CLI": {"means": "customer identifier"},
            "FL_ATIVO": {"means": "active flag AT THE TIME OF THE EVENT"},
            "DT_EVT": {"means": "event timestamp"},
            "TP_EVT": {"means": "event type"},
        },
    },
    "MART.DIM_CUSTOMER": {
        "description": "Certified customer dimension. The answer to any question "
                       "about how many customers we have.",
        "grain": "one row per customer per validity window (SCD2)",
        "certified": True,
        "business_terms": {"active customer": "is_active", "customer": "customer_key"},
        "filters_required": ["valid_to IS NULL"],
        "columns": {
            "customer_key": {"means": "surrogate customer key"},
            "customer_name": {"means": "customer name", "pii": True},
            "is_active": {"means": "active customer, reconciled against churn"},
            "valid_from": {"means": "start of the validity window"},
            "valid_to": {"means": "end of the validity window, NULL means current"},
            "tax_id": {"means": "national tax identifier", "pii": True,
                       "restricted": True},
        },
    },
}

QUESTIONS = {
    "active customers": ["active", "customer", "cliente", "atv", "ativo"],
    "revenue": ["revenue", "amount", "valor", "vlr"],
}


def _score(name: str, terms: list) -> int:
    n = re.sub(r"[^a-z]", " ", name.lower())
    return sum(1 for t in terms if t in n)


def ask_raw(question: str) -> dict:
    """What an agent can do with names alone: match strings and hope.

    It is not being stupid. It is doing the only thing available to it.
    """
    terms = QUESTIONS[question]
    ranked = []
    for table, meta in RAW.items():
        best_col, best = None, 0
        for col in meta["columns"]:
            s = _score(col, terms)
            if s > best:
                best_col, best = col, s
        ranked.append({"table": table, "column": best_col,
                       "score": best + _score(table, terms),
                       "rows": meta["rows"]})
    ranked.sort(key=lambda r: (-r["score"], r["table"]))
    return {
        "mode": "raw schema",
        "candidates": ranked,
        "picked": ranked[0],
        "why": "highest string overlap between the question and the column name",
        "warnings": [
            "%d of %d tables scored as plausible; nothing in the schema breaks the tie"
            % (sum(1 for r in ranked if r["score"] > 0), len(ranked)),
            "no grain declared, so a row could be a customer or an event",
            "no certification declared, so a staging table and a mart rank equally",
            "no PII marking, so a restricted column can be selected without anyone noticing",
        ],
    }


def ask_contract(question: str) -> dict:
    """With a contract the agent stops guessing and starts resolving."""
    # Business terms are declared in the singular, questions arrive in either
    # number. Normalizing here rather than declaring both spellings keeps the
    # glossary from growing a synonym per phrasing.
    variants = {question, question.rstrip("s"), question + "s"}
    hits = []
    for table, meta in CONTRACT.items():
        terms = meta.get("business_terms", {})
        match = next((t for t in variants if t in terms), None)
        if match:
            hits.append((table, meta, terms[match]))
    if not hits:
        return {
            "mode": "semantic contract",
            "picked": None,
            "why": "no certified table declares a business term for %r" % question,
            "answerable": False,
            "next_step": "this is the good failure: the agent says the term is "
                         "undefined instead of inventing a number from a column "
                         "whose name happened to match",
            "warnings": [],
        }
    certified = [h for h in hits if h[1]["certified"]] or hits
    table, meta, column = certified[0]
    restricted = [c for c, m in meta["columns"].items() if m.get("restricted")]
    return {
        "mode": "semantic contract",
        "picked": {"table": table, "column": column, "rows": None},
        "why": "the business term %r resolves to %s.%s on the certified table"
               % (question, table, column),
        "answerable": True,
        "grain": meta["grain"],
        "filters_required": meta.get("filters_required", []),
        "restricted_columns": restricted,
        "warnings": [],
    }


def compare(question: str) -> dict:
    raw = ask_raw(question)
    con = ask_contract(question)
    # Agreement means the same table AND the same column. Comparing the table
    # alone reports agreement on exactly the case that matters most: right
    # table, wrong column, plausible number.
    picked = con.get("picked") or {}
    same = (picked.get("table") == raw["picked"]["table"]
            and picked.get("column") == raw["picked"]["column"])
    return {"question": question, "raw": raw, "contract": con, "agree": same}


def self_test() -> int:
    n = 0
    r = compare("active customers")

    # The subtle failure, and the reason this demo is worth doing live: name
    # matching lands on the RIGHT table and the WRONG column. It picks the
    # surrogate key over the active flag, because "customer_key" overlaps the
    # question more than "is_active" does.
    assert r["raw"]["picked"]["table"] == "MART.DIM_CUSTOMER", r["raw"]["picked"]; n += 1
    assert r["raw"]["picked"]["column"] == "customer_key", r["raw"]["picked"]; n += 1
    # All three tables look plausible on names alone, which is the real problem.
    assert sum(1 for c in r["raw"]["candidates"] if c["score"] > 0) == 3; n += 1

    # The contract resolves the business term to the flag, not the key.
    assert r["contract"]["picked"]["table"] == "MART.DIM_CUSTOMER"; n += 1
    assert r["contract"]["picked"]["column"] == "is_active"; n += 1
    # Same table, different column: the two paths agree on WHERE and disagree on
    # WHAT, which is the hardest kind of wrong answer to catch in review.
    assert r["raw"]["picked"]["table"] == r["contract"]["picked"]["table"]; n += 1
    assert r["raw"]["picked"]["column"] != r["contract"]["picked"]["column"]; n += 1
    # And agreement must be judged on table AND column. Comparing the table
    # alone reported agreement here, which was a real defect in this file: it
    # hid the exact failure the demo exists to show.
    assert r["agree"] is False, "agreement must consider the column too"; n += 1
    # And it carries the two things a correct query cannot be written without.
    assert r["contract"]["grain"].startswith("one row per customer"); n += 1
    assert r["contract"]["filters_required"] == ["valid_to IS NULL"]; n += 1
    # Without that filter an SCD2 dimension counts every historical version of
    # every customer, so the number comes out high and looks perfectly credible.
    assert CONTRACT["MART.DIM_CUSTOMER"]["grain"].count("SCD2") == 1; n += 1
    # Restricted columns are declared, so the agent can be told to refuse them.
    assert r["contract"]["restricted_columns"] == ["tax_id"]; n += 1

    # A term nobody defined is refused instead of answered from a name match.
    r2 = compare("revenue")
    assert r2["contract"]["answerable"] is False; n += 1
    assert r2["contract"]["picked"] is None; n += 1
    # The raw path still confidently returns something. That is the danger.
    assert r2["raw"]["picked"]["table"] is not None; n += 1

    # Row counts differ by 15x across the plausible tables, so picking wrong is
    # not a rounding error, it is a different answer.
    rows = sorted(c["rows"] for c in r["raw"]["candidates"])
    assert rows[-1] / rows[0] > 15, rows; n += 1

    print("data_for_agents self-test OK (%d asserts)" % n)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="The same question, the same tables, two kinds of metadata.")
    p.add_argument("--question", default="active customers", choices=sorted(QUESTIONS))
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()

    if a.self_test:
        raise SystemExit(self_test())

    res = compare(a.question)
    if a.json:
        print(json.dumps(res, indent=2))
        raise SystemExit(0)

    print('question: "how many %s do we have?"\n' % a.question)
    print("WITHOUT A CONTRACT (names only)")
    for c in res["raw"]["candidates"]:
        mark = " <- picked" if c is res["raw"]["picked"] else ""
        print("  %-22s %-14s score %d   %11s rows%s"
              % (c["table"], c["column"] or "-", c["score"], f"{c['rows']:,}", mark))
    print("  why: %s" % res["raw"]["why"])
    for w in res["raw"]["warnings"]:
        print("  ! %s" % w)

    print("\nWITH A SEMANTIC CONTRACT")
    con = res["contract"]
    if con["answerable"]:
        print("  %s.%s" % (con["picked"]["table"], con["picked"]["column"]))
        print("  why: %s" % con["why"])
        print("  grain: %s" % con["grain"])
        print("  required filter: %s" % ", ".join(con["filters_required"]))
        print("  restricted, must not be selected: %s"
              % ", ".join(con["restricted_columns"]))
    else:
        print("  refuses to answer: %s" % con["why"])
        print("  %s" % con["next_step"])

    print("\n  the two paths disagree: %s" % (not res["agree"]))
    print("  same model, same tables. The only thing that changed is the metadata.")
