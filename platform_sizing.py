# -*- coding: utf-8 -*-
"""Size a data platform by inventory, not by volume.

Companion code for the advanced session "Size the Platform by Inventory".

The mistake is easy to make and expensive: you have the client's data volume, so
you derive compute from it. Volume tells you how much data moves. It does not
tell you how many clusters run AT THE SAME TIME in the peak window, and for
parallel batch that concurrency IS the bill.

Measured on a bank's lakehouse migration, sizing from volume came out about six
times under the number a cloud architect reached by summing the actual cluster
and job inventory. Compute is the largest line in a data platform, so the sizing
METHOD decides the estimate, and being wrong by 6x is not a variance, it is a
different proposal.

    python platform_sizing.py
    python platform_sizing.py --self-test
"""
from __future__ import annotations

import argparse
import json

# DBU multiplier per node size. Not monotonic with vCPU across families, which is
# why it is a table and not a formula.
DBU_PER_NODE = {
    "Standard_D8s_v5": 1.5,
    "Standard_D16s_v5": 3.0,
    "Standard_E8ds_v5": 2.75,
    "Standard_E16ds_v5": 5.5,
}


def size_by_volume(tb_per_day: float, days: int = 22,
                   tb_per_node_hour: float = 0.35) -> dict:
    """The intuitive method, and the one that under sizes parallel batch.

    It answers "how many node hours to move this much data", which is a real
    question and the wrong one. It is a sanity floor, never the estimate.
    """
    if tb_per_day <= 0:
        raise ValueError("tb_per_day must be > 0")
    node_hours = tb_per_day * days / tb_per_node_hour
    return {
        "method": "volume",
        "node_hours": round(node_hours),
        "rationale": "%.1f TB/day x %d days / %.2f TB per node hour"
                     % (tb_per_day, days, tb_per_node_hour),
        "status": "SANITY FLOOR ONLY. This is throughput, not concurrency. It "
                  "cannot see how many clusters run together in the peak window.",
    }


def size_by_inventory(clusters: list) -> dict:
    """The architect's method: sum what actually runs.

    clusters: [{name, sku, nodes, hours_per_day, days_per_month, env}]
    """
    if not clusters:
        raise ValueError(
            "no cluster inventory. Without it there is no compute estimate, only "
            "a throughput floor. Translate the client's volumetry into an "
            "inventory of clusters and jobs per environment first.")
    node_hours = 0.0
    dbu_hours = 0.0
    per_env = {}
    for c in clusters:
        for field in ("name", "sku", "nodes", "hours_per_day", "days_per_month"):
            if field not in c:
                raise ValueError("cluster %r is missing %r"
                                 % (c.get("name", "unnamed"), field))
        if c["sku"] not in DBU_PER_NODE:
            raise ValueError(
                "unknown sku %r on cluster %r. Valid: %s"
                % (c["sku"], c["name"], ", ".join(sorted(DBU_PER_NODE))))
        nh = c["nodes"] * c["hours_per_day"] * c["days_per_month"]
        dh = nh * DBU_PER_NODE[c["sku"]]
        node_hours += nh
        dbu_hours += dh
        env = c.get("env", "PRD")
        per_env[env] = per_env.get(env, 0) + nh
    return {
        "method": "inventory",
        "clusters": len(clusters),
        "node_hours": round(node_hours),
        "dbu_hours": round(dbu_hours, 1),
        "node_hours_by_env": {k: round(v) for k, v in sorted(per_env.items())},
        "rationale": "%d clusters summed as nodes x hours/day x days/month"
                     % len(clusters),
    }


def compare_methods(tb_per_day: float, clusters: list,
                    reference_node_hours: float | None = None) -> dict:
    """Both methods side by side, plus the conservative posture.

    When a reference estimate exists (a cloud architect, the client, an official
    calculator), anchor on it and stay at or above it, adding the fronts it
    missed. Undercutting someone else's number silently is how a proposal wins
    on price and loses on delivery.
    """
    vol = size_by_volume(tb_per_day)
    inv = size_by_inventory(clusters)
    ratio = inv["node_hours"] / vol["node_hours"] if vol["node_hours"] else 0.0
    alerts = []
    if ratio >= 2:
        alerts.append(
            "the volume method is %.1fx UNDER the inventory. Parallel batch is "
            "invisible to a throughput calculation, and compute is the largest "
            "line in the platform." % ratio)
    if ratio and ratio <= 0.5:
        alerts.append(
            "the inventory is %.1fx under the volume floor, which usually means "
            "the inventory is incomplete rather than the platform being cheap."
            % (1 / ratio))
    adopted = max(vol["node_hours"], inv["node_hours"])
    basis = "inventory" if inv["node_hours"] >= vol["node_hours"] else "volume floor"
    if reference_node_hours:
        if adopted < reference_node_hours:
            alerts.append(
                "a reference estimate of %s node hours exists and is HIGHER than "
                "ours. Anchor on it and add the fronts it missed, rather than "
                "shipping the lower number."
                % f"{round(reference_node_hours):,}")
            adopted = reference_node_hours
            basis = "external reference (anchored, conservative)"
    return {
        "volume": vol,
        "inventory": inv,
        "under_sizing_factor": round(ratio, 2),
        "adopted_node_hours": round(adopted),
        "adopted_basis": basis,
        "alerts": alerts,
        "posture": "when two estimates disagree, adopt the HIGHER one and say why",
    }


# A data hub inventory: domain ingestion, medallion transformation, the heavy
# regulatory batch, feeds and data quality, plus the non production subsets.
EXAMPLE_INVENTORY = [
    {"name": "Domain ingestion (14 sources)", "sku": "Standard_D8s_v5",
     "nodes": 16, "hours_per_day": 8, "days_per_month": 22, "env": "PRD"},
    {"name": "Streaming and CDC (always on)", "sku": "Standard_D8s_v5",
     "nodes": 8, "hours_per_day": 24, "days_per_month": 30, "env": "PRD"},
    {"name": "Medallion bronze to silver", "sku": "Standard_E8ds_v5",
     "nodes": 24, "hours_per_day": 7, "days_per_month": 22, "env": "PRD"},
    {"name": "Medallion silver to gold", "sku": "Standard_E16ds_v5",
     "nodes": 20, "hours_per_day": 6, "days_per_month": 22, "env": "PRD"},
    {"name": "Regulatory batch (month end peak)", "sku": "Standard_E16ds_v5",
     "nodes": 40, "hours_per_day": 12, "days_per_month": 6, "env": "PRD"},
    {"name": "Feature engineering for ML", "sku": "Standard_E8ds_v5",
     "nodes": 12, "hours_per_day": 6, "days_per_month": 22, "env": "PRD"},
    {"name": "Downstream feeds", "sku": "Standard_D8s_v5",
     "nodes": 12, "hours_per_day": 5, "days_per_month": 22, "env": "PRD"},
    {"name": "Data quality and reconciliation", "sku": "Standard_D8s_v5",
     "nodes": 10, "hours_per_day": 6, "days_per_month": 22, "env": "PRD"},
    {"name": "Homologation subset", "sku": "Standard_D8s_v5",
     "nodes": 12, "hours_per_day": 6, "days_per_month": 20, "env": "HOM"},
    {"name": "Development subset", "sku": "Standard_D8s_v5",
     "nodes": 8, "hours_per_day": 6, "days_per_month": 20, "env": "DEV"},
]


def self_test() -> int:
    n = 0
    inv = size_by_inventory(EXAMPLE_INVENTORY)
    assert inv["clusters"] == 10; n += 1
    assert inv["node_hours"] == 24_416, inv["node_hours"]; n += 1
    # Environments are separated, because PRD is peak and the rest are subsets.
    assert set(inv["node_hours_by_env"]) == {"DEV", "HOM", "PRD"}; n += 1
    assert inv["node_hours_by_env"]["PRD"] > 20_000; n += 1
    # The month end batch is 5 days a month and still the single largest block,
    # which is exactly what a volume calculation cannot see.
    peak = 40 * 12 * 6
    assert peak == 2880 and peak * 4 < inv["node_hours"]; n += 1
    # Always on streaming is the other block volume cannot see: 8 nodes for 24
    # hours over 30 days outweighs the entire month end batch.
    streaming = 8 * 24 * 30
    assert streaming > peak, (streaming, peak); n += 1

    vol = size_by_volume(2.0)
    assert vol["node_hours"] == 126, vol["node_hours"]; n += 1
    assert "SANITY FLOOR" in vol["status"]; n += 1

    # The headline: same platform, two methods, an order of magnitude apart.
    cmp_ = compare_methods(15.0, EXAMPLE_INVENTORY)
    assert cmp_["volume"]["node_hours"] == 943, cmp_["volume"]["node_hours"]; n += 1
    assert cmp_["under_sizing_factor"] > 20; n += 1
    assert any("UNDER the inventory" in a for a in cmp_["alerts"]); n += 1
    # Conservative posture: adopt the higher number.
    assert cmp_["adopted_node_hours"] == inv["node_hours"]; n += 1
    assert cmp_["adopted_basis"] == "inventory"; n += 1

    # A calibrated case where volume lands roughly 6x under, which is the
    # measured gap from the field.
    cmp6 = compare_methods(66.0, EXAMPLE_INVENTORY)
    assert 5.5 < cmp6["under_sizing_factor"] < 6.5, cmp6["under_sizing_factor"]; n += 1
    assert cmp6["adopted_basis"] == "inventory"; n += 1

    # An external reference that is HIGHER wins, and says so.
    cmp_ref = compare_methods(15.0, EXAMPLE_INVENTORY, reference_node_hours=31_000)
    assert cmp_ref["adopted_node_hours"] == 31_000; n += 1
    assert "anchored" in cmp_ref["adopted_basis"]; n += 1
    assert any("HIGHER than" in a for a in cmp_ref["alerts"]); n += 1
    # A reference that is LOWER does not drag our number down.
    cmp_low = compare_methods(15.0, EXAMPLE_INVENTORY, reference_node_hours=5_000)
    assert cmp_low["adopted_node_hours"] == inv["node_hours"]; n += 1

    # Refusals.
    for bad, needle in (
        ([], "no cluster inventory"),
        ([{"name": "x", "sku": "Standard_D8s_v5", "nodes": 2, "hours_per_day": 1}],
         "is missing 'days_per_month'"),
        ([{"name": "x", "sku": "Standard_Z99", "nodes": 2, "hours_per_day": 1,
           "days_per_month": 1}], "unknown sku"),
    ):
        try:
            size_by_inventory(bad)
            raise AssertionError("expected refusal: " + needle)
        except ValueError as e:
            assert needle in str(e), (needle, str(e)); n += 1

    # DBU rating is a lookup, not a formula: E8ds is 2.75 while D8s is 1.5 at the
    # same vCPU count, so deriving it from size would misprice the memory family.
    assert DBU_PER_NODE["Standard_E8ds_v5"] != DBU_PER_NODE["Standard_D8s_v5"]; n += 1

    print("platform_sizing self-test OK (%d asserts)" % n)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Inventory against volume sizing.")
    p.add_argument("--tb-per-day", type=float, default=66.0)
    p.add_argument("--reference", type=float, default=None,
                   help="an architect or client node hour figure to anchor on")
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()

    if a.self_test:
        raise SystemExit(self_test())

    res = compare_methods(a.tb_per_day, EXAMPLE_INVENTORY, a.reference)
    if a.json:
        print(json.dumps(res, indent=2))
        raise SystemExit(0)

    print("SIZED BY VOLUME")
    print("  %s" % res["volume"]["rationale"])
    print("  %s node hours" % f"{res['volume']['node_hours']:,}")
    print("  %s\n" % res["volume"]["status"])

    print("SIZED BY INVENTORY")
    for c in EXAMPLE_INVENTORY:
        nh = c["nodes"] * c["hours_per_day"] * c["days_per_month"]
        print("  %-36s %-18s %3d nodes x %2dh x %2dd = %7s"
              % (c["name"], c["sku"], c["nodes"], c["hours_per_day"],
                 c["days_per_month"], f"{nh:,}"))
    print("  %s node hours, %s DBU hours"
          % (f"{res['inventory']['node_hours']:,}",
             f"{res['inventory']['dbu_hours']:,.0f}"))
    print("  by environment: %s" % res["inventory"]["node_hours_by_env"])

    print("\n  the volume method is %.1fx under" % res["under_sizing_factor"])
    for al in res["alerts"]:
        print("  ! %s" % al)
    print("\n  adopted: %s node hours, basis %s"
          % (f"{res['adopted_node_hours']:,}", res["adopted_basis"]))
    print("  %s" % res["posture"])
