# -*- coding: utf-8 -*-
"""Resolve model prices from the vendors' own public sources, at run time.

Stdlib only. No API key, no account: every source here is public.

    python live_prices.py                  # fetch, show, write prices.live.json
    python live_prices.py --check          # compare the hand table against live
    python live_prices.py --self-test      # offline, against recorded fixtures

Purpose. A manually transcribed price table can diverge from the vendor's price
without any visible sign: the model name remains correct while the price
changes, and a check that compares names cannot detect it. The table originally
published with this repository recorded Claude Sonnet 5 at USD 3/15 per million
tokens while the vendor page stated USD 2/10, and GPT-4.1 at USD 5/15 while Azure
charged USD 2/8. No check failed, and every estimate based on it was incorrect.

Three sources, each read the way it publishes:

    Anthropic pricing page    markdown table, one row per model, USD per MTok
    Anthropic caching page    the minimum cacheable prompt length per model
    Azure Retail Prices API   one meter per SKU, input and output separate

Rules the code enforces rather than suggests:

1. A model is matched by the EXACT name its source publishes, declared in the
   price file. Substring matching is how a "priority processing" SKU, a batch SKU
   or a long context SKU gets read as the standard price, sometimes several
   times off. If the declared name is gone, the run refuses and lists what the
   source does publish.
2. Units are read, never assumed. Azure publishes per 1K for older models and
   per 1M for newer ones, in the same API. An unknown unit refuses.
3. No silent fallback. If a source cannot be reached, the last successful fetch
   is used only with its age printed next to it, and with no previous fetch the
   run refuses instead of reverting to the hand table without stating so.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ANTHROPIC_PRICING = "https://platform.claude.com/docs/en/about-claude/pricing.md"
ANTHROPIC_CACHING = "https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md"
AZURE_PRICES = "https://prices.azure.com/api/retail/prices"
USER_AGENT = "agent-cost-math (public price reader; github.com/julianamarialop)"
PRICE_FIELDS = ("input_per_1k", "output_per_1k", "cache_read_per_1k", "cache_write_per_1k")


class PriceUnavailable(RuntimeError):
    """A price that cannot be resolved is an error, never a zero."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_hours(iso: str, now: str | None = None) -> float | None:
    try:
        a = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        b = _dt.datetime.strptime(now or _now(), "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds() / 3600.0


def http_get(url: str, timeout: float = 30.0, max_wait: float = 150.0) -> str:
    """GET with the retry policy a shared public API requires.

    Azure answers 429 with a Retry-After of about a minute under load, which is
    the expected situation when many participants execute the same command.
    The call waits for the interval the API indicates, since repeated requests
    would extend the wait for everyone. After `max_wait` seconds it stops with an
    explicit error instead of blocking indefinitely."""
    waited = 0.0
    while True:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json, text/markdown, */*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            wait = float(e.headers.get("Retry-After") or 30)
            if waited + wait > max_wait:
                raise PriceUnavailable(
                    "%s keeps answering 429 (rate limited) after %.0f s of waiting"
                    % (urllib.parse.urlsplit(url).netloc, waited))
            print("  rate limited by %s, waiting %.0f s as it asks"
                  % (urllib.parse.urlsplit(url).netloc, wait), file=sys.stderr)
            time.sleep(wait)
            waited += wait


# ---------------------------------------------------------------------------
# Anthropic: the pricing page and the caching page
# ---------------------------------------------------------------------------
_ANTHROPIC_COLUMNS = (
    ("base input", "input_per_1k"),
    ("output tokens", "output_per_1k"),
    ("5m cache write", "cache_write_per_1k"),
    ("cache hits", "cache_read_per_1k"),
)


def _per_mtok(cell: str) -> float | None:
    """'$12.50 / MTok<sup>1</sup>' -> price per 1K tokens, or None.

    The footnote is glued to the unit, so the number is read before the slash
    and whatever follows it never enters the arithmetic."""
    m = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*/\s*MTok", cell or "", re.I)
    return float(m.group(1).replace(",", "")) / 1000.0 if m else None


def _model_cell(cell: str) -> tuple[str, bool]:
    """Model cell -> (name, retired). Retired rows stay on the page with their
    old, higher prices, which is exactly where a stale 15/75 hides."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell or "")
    retired = "retired" in text.lower() or "deprecat" in text.lower()
    name = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip().strip("*").strip()
    return name, retired


def parse_anthropic_pricing(text: str) -> dict:
    """Every per model price table on the page, recognized by its header.

    Tables are found by column names rather than section titles, because a
    section title is rewritten more often than a column header."""
    out: dict = {}
    lines = text.splitlines()
    cols = None
    for i, line in enumerate(lines):
        raw = line.strip()
        if not raw.startswith("|"):
            cols = None
            continue
        cells = [c.strip() for c in raw.strip("|").split("|")]
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if nxt.startswith("|") and set(nxt) <= set("|-: "):
            low = [c.lower() for c in cells]
            found = {}
            for j, head in enumerate(low):
                for mark, field in _ANTHROPIC_COLUMNS:
                    if mark in head:
                        found[field] = j
            cols = found if {"input_per_1k", "output_per_1k"} <= set(found) else None
            continue
        if not cols:
            continue
        name, retired = _model_cell(cells[0])
        if not name.lower().startswith("claude"):
            continue
        row = out.setdefault(name, {"retired": retired})
        for field, j in cols.items():
            if j < len(cells):
                v = _per_mtok(cells[j])
                if v is not None:
                    row[field] = v
    return {n: r for n, r in out.items() if "input_per_1k" in r and "output_per_1k" in r}


def parse_anthropic_floors(text: str) -> dict:
    """'* 4,096 tokens for Claude Haiku 4.5' -> {'Claude Haiku 4.5': 4096}.

    The floor matters more than it looks: below it the API does not cache and
    does not say so, and the floor is NOT monotonic across generations (the
    small Haiku 4.5 needs eight times the prompt that Opus 5 does)."""
    floors: dict = {}
    for line in text.splitlines():
        m = re.match(r"\s*[*-]\s*([0-9][0-9,]*)\s+tokens for\s+(.+)$", line)
        if not m:
            continue
        n = int(m.group(1).replace(",", ""))
        names = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2))
        names = re.sub(r"\([^)]*\)", "", names)
        for part in re.split(r",|\band\b", names):
            part = part.strip().rstrip(".").strip()
            if part.lower().startswith("claude"):
                floors.setdefault(part, n)
    return floors


# ---------------------------------------------------------------------------
# Azure Retail Prices API
# ---------------------------------------------------------------------------
def _azure_per_1k(price: float, unit: str) -> float:
    u = (unit or "").replace(" ", "").upper()
    if u == "1K":
        return float(price)
    if u == "1M":
        return float(price) / 1000.0
    raise PriceUnavailable(
        "Azure meter with unit %r: refusing to guess the conversion" % unit)


def azure_query(skus: list, region: str, fetch=None) -> list:
    """One request for every declared SKU, following NextPageLink.

    The filter is by exact skuName and region, so a busy shared API answers a
    handful of rows instead of thousands of pages."""
    fetch = fetch or http_get
    ors = " or ".join("skuName eq '%s'" % s.replace("'", "''") for s in sorted(set(skus)))
    flt = "armRegionName eq '%s' and priceType eq 'Consumption' and (%s)" % (region, ors)
    url = AZURE_PRICES + "?" + urllib.parse.urlencode({"$filter": flt, "currencyCode": "USD"})
    rows = []
    while url:
        page = json.loads(fetch(url))
        rows.extend(page.get("Items", []))
        url = page.get("NextPageLink")
    return rows


def resolve_azure(declared: dict, rows: list) -> dict:
    """Map the declared SKUs of one model to prices, refusing ambiguity."""
    out = {}
    for field, sku in declared["skus"].items():
        hits = [r for r in rows if r.get("skuName") == sku
                and (not declared.get("product") or r.get("productName") == declared["product"])]
        prices = {(_azure_per_1k(r["retailPrice"], r.get("unitOfMeasure")))
                  for r in hits}
        if not hits:
            near = sorted({r.get("skuName") for r in rows})
            raise PriceUnavailable(
                "Azure no longer publishes SKU %r (%s) in %s. SKUs returned: %s"
                % (sku, field, declared.get("product", "?"), near or "none"))
        if len(prices) > 1:
            raise PriceUnavailable(
                "Azure publishes %d different prices for SKU %r: %s. Refusing to "
                "pick one silently." % (len(prices), sku, sorted(prices)))
        out[field] = prices.pop()
    if "cache_write_per_1k" not in out and declared.get("cache_write") == "input":
        # Azure OpenAI has no write surcharge: a cache miss is billed as input.
        out["cache_write_per_1k"] = out["input_per_1k"]
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def fetch_live(base: dict, fetch=None, region: str | None = None) -> dict:
    """Build a price table with the same shape as the hand table, every number
    replaced by what its source publishes right now.

    Structure that no price source publishes (tier, notes) is kept from the base
    table and the output states so, to prevent it from being read as retrieved."""
    fetch = fetch or http_get
    region = region or base.get("azure_region", "eastus2")
    models = base["models"]
    want_anthropic = [n for n, m in models.items()
                      if m.get("source", {}).get("route") == "anthropic"]
    want_azure = {n: m["source"] for n, m in models.items()
                  if m.get("source", {}).get("route") == "azure"}
    undeclared = [n for n, m in models.items() if not m.get("source")]
    if undeclared:
        raise PriceUnavailable("models with no declared source: %s" % undeclared)

    fetched_at = _now()
    out = {"provenance": "fetched from the vendors' public sources",
           "pricing_as_of": fetched_at, "unit": "USD per 1,000 tokens",
           "azure_region": region, "sources": {}, "models": {}}

    if want_anthropic:
        prices = parse_anthropic_pricing(fetch(ANTHROPIC_PRICING))
        if not prices:
            raise PriceUnavailable("the Anthropic pricing page answered but no price "
                                   "table was recognized (the page changed shape)")
        floors = parse_anthropic_floors(fetch(ANTHROPIC_CACHING))
        out["sources"]["anthropic"] = [ANTHROPIC_PRICING, ANTHROPIC_CACHING]
        for name in want_anthropic:
            page_name = models[name]["source"].get("name", name)
            row = prices.get(page_name)
            if row is None:
                raise PriceUnavailable(
                    "%r is not on the Anthropic pricing page. Published: %s"
                    % (page_name, ", ".join(sorted(prices))))
            entry = {k: v for k, v in models[name].items() if k not in PRICE_FIELDS}
            entry.update({k: row[k] for k in PRICE_FIELDS if k in row})
            if page_name in floors:
                entry["min_cache_tokens"] = floors[page_name]
                entry["min_cache_tokens_source"] = "fetched"
            else:
                entry["min_cache_tokens_source"] = "declared (not found on the caching page)"
            if row.get("retired"):
                entry["warning"] = "marked RETIRED on the vendor page"
            entry["price_source"] = ANTHROPIC_PRICING
            out["models"][name] = entry

    if want_azure:
        skus = [s for d in want_azure.values() for s in d["skus"].values()]
        rows = azure_query(skus, region, fetch)
        out["sources"]["azure"] = AZURE_PRICES
        for name, declared in want_azure.items():
            entry = {k: v for k, v in models[name].items() if k not in PRICE_FIELDS}
            entry.update(resolve_azure(declared, rows))
            entry["min_cache_tokens_source"] = "declared"
            entry["price_source"] = "%s (%s, %s)" % (AZURE_PRICES, declared.get("product"), region)
            out["models"][name] = entry
    return out


def load_with_fallback(base: dict, cache_path: str, fetch=None) -> tuple:
    """Live first. On failure, the last successful fetch WITH its age. Never the
    hand table in disguise. Returns (prices, notes)."""
    try:
        live = fetch_live(base, fetch)
    except Exception as e:                              # network, 429, page change
        reason = "%s: %s" % (type(e).__name__, e)
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
        except (OSError, ValueError):
            raise PriceUnavailable(
                "could not fetch live prices (%s) and there is no previous fetch at "
                "%s. Run without --live to use the hand table, which says what it is."
                % (reason, cache_path))
        age = _age_hours(cached.get("pricing_as_of", ""))
        cached["provenance"] = "previous fetch, NOT live (%s old)" % (
            "%.1f h" % age if age is not None else "age unknown")
        return cached, ["live fetch failed (%s); using the fetch from %s"
                        % (reason, cached.get("pricing_as_of", "?"))]
    try:
        with open(cache_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(live, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
    except OSError as e:
        return live, ["fetched, but could not save %s (%s)" % (cache_path, e)]
    return live, []


def diff(base: dict, live: dict, tolerance: float = 1e-9) -> list:
    """Every field where the hand table disagrees with the source."""
    rows = []
    for name, m in base["models"].items():
        lm = live["models"].get(name)
        if lm is None:
            rows.append((name, "model", "present", "absent from live"))
            continue
        for f in PRICE_FIELDS + ("min_cache_tokens",):
            a, b = m.get(f), lm.get(f)
            if a is None and b is None:
                continue
            if a is None or b is None or abs(float(a) - float(b)) > tolerance:
                rows.append((name, f, a, b))
    return rows


def render(prices: dict) -> str:
    head = ("model", "in /1M", "out /1M", "cache read", "cache write", "floor")
    rows = [head]
    for name, m in prices["models"].items():
        def per_m(v):
            return "-" if v is None else ("%.3f" % (v * 1000)).rstrip("0").rstrip(".")
        rows.append((name, per_m(m.get("input_per_1k")), per_m(m.get("output_per_1k")),
                     per_m(m.get("cache_read_per_1k")), per_m(m.get("cache_write_per_1k")),
                     f"{m['min_cache_tokens']:,}" if m.get("min_cache_tokens") else "-"))
    w = [max(len(str(r[i])) for r in rows) for i in range(len(head))]
    lines = ["  ".join(str(r[i]).ljust(w[i]) if i == 0 else str(r[i]).rjust(w[i])
                       for i in range(len(head))) for r in rows]
    lines.insert(1, "  ".join("-" * x for x in w))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self test, offline, against fixtures shaped like the real responses
# ---------------------------------------------------------------------------
_FIX_PRICING = """\
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits & refreshes | Output tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Claude Opus 5 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Opus 4.1 ([retired, except on Bedrock](https://x)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Sonnet 5 | $2 / MTok<sup>3</sup> | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
| Claude Haiku 4.5 | $1 / MTok | $1.25 / MTok | $2 / MTok | $0.10 / MTok | $5 / MTok |

| Concept | Details |
| --- | --- |
| **Billing unit** | Claude Consumption Unit |
"""
_FIX_CACHING = """\
* 512 tokens for Claude Opus 5, [Claude Mythos 5](https://x), Claude Sonnet 5.5
* 1,024 tokens for Claude Opus 4.8, Claude Sonnet 5, Claude Sonnet 4.5 ([deprecated](https://x))
* 4,096 tokens for Claude Haiku 4.5
"""


def _fix_azure(rows):
    def fetch(url):
        if url.startswith(AZURE_PRICES):
            return json.dumps({"Items": rows, "NextPageLink": None})
        if url == ANTHROPIC_PRICING:
            return _FIX_PRICING
        if url == ANTHROPIC_CACHING:
            return _FIX_CACHING
        raise AssertionError(url)
    return fetch


def _row(product, sku, price, unit):
    return {"productName": product, "skuName": sku, "retailPrice": price,
            "unitOfMeasure": unit, "armRegionName": "eastus2", "type": "Consumption"}


_FIX_ROWS = [
    _row("Azure OpenAI", "gpt 4.1 Inp glbl", 0.002, "1K"),
    _row("Azure OpenAI", "gpt 4.1 Outp glbl", 0.008, "1K"),
    _row("Azure OpenAI", "gpt 4.1 cached Inp glbl", 0.0005, "1K"),
    _row("Azure OpenAI GPT5", "5.4 inp Gl", 2.5, "1M"),
    _row("Azure OpenAI GPT5", "5.4 opt Gl", 15.0, "1M"),
    _row("Azure OpenAI GPT5", "5.4 cd inp Gl", 0.25, "1M"),
    # Same family, priority processing, twice the price: must not be read.
    _row("Azure OpenAI GPT5", "5.4 pp inp Gl", 5.0, "1M"),
]


def self_test() -> int:
    n = 0
    p = parse_anthropic_pricing(_FIX_PRICING)
    assert p["Claude Opus 5"]["input_per_1k"] == 0.005; n += 1
    assert p["Claude Opus 5"]["output_per_1k"] == 0.025; n += 1
    assert p["Claude Opus 5"]["cache_write_per_1k"] == 0.00625; n += 1
    assert p["Claude Opus 5"]["cache_read_per_1k"] == 0.0005; n += 1
    # The footnote glued to the unit does not leak into the number.
    assert p["Claude Sonnet 5"]["input_per_1k"] == 0.002; n += 1
    # The retired row keeps its old price and is flagged, not dropped.
    assert p["Claude Opus 4.1"]["retired"] is True; n += 1
    assert p["Claude Opus 4.1"]["input_per_1k"] == 0.015; n += 1
    # A non price table on the same page is ignored.
    assert all(k.startswith("Claude") for k in p); n += 1

    f = parse_anthropic_floors(_FIX_CACHING)
    assert f["Claude Haiku 4.5"] == 4096; n += 1
    assert f["Claude Opus 5"] == 512; n += 1
    assert f["Claude Sonnet 5"] == 1024; n += 1
    assert f["Claude Mythos 5"] == 512; n += 1          # link text, not the URL
    assert f["Claude Sonnet 4.5"] == 1024; n += 1       # parenthetical removed

    assert _azure_per_1k(0.002, "1K") == 0.002; n += 1
    assert _azure_per_1k(2.5, "1M") == 0.0025; n += 1
    try:
        _azure_per_1k(1.0, "1 Hour")
        raise AssertionError("unknown unit must refuse")
    except PriceUnavailable:
        n += 1

    with open(os.path.join(HERE, "prices.example.json"), encoding="utf-8") as fh:
        base = json.load(fh)
    live = fetch_live(base, _fix_azure(_FIX_ROWS))
    m = live["models"]
    assert m["GPT-4.1"]["input_per_1k"] == 0.002; n += 1
    assert m["GPT-4.1"]["output_per_1k"] == 0.008; n += 1
    assert m["GPT-4.1"]["cache_write_per_1k"] == 0.002; n += 1     # miss billed as input
    # Exact SKU match: the priority processing row at twice the price is not read.
    assert m["GPT-5.4"]["input_per_1k"] == 0.0025; n += 1
    assert m["GPT-5.4"]["output_per_1k"] == 0.015; n += 1
    assert m["Claude Haiku 4.5"]["min_cache_tokens"] == 4096; n += 1
    assert m["Claude Haiku 4.5"]["min_cache_tokens_source"] == "fetched"; n += 1
    # Structure no source publishes is carried from the base table.
    assert m["Claude Opus 5"]["tier"] == "high"; n += 1
    assert live["pricing_as_of"].endswith("Z"); n += 1
    # The shipped hand table agrees with the sources it declares.
    assert diff(base, live) == [], diff(base, live); n += 1

    # A drifted hand table is caught field by field.
    drifted = json.loads(json.dumps(base))
    drifted["models"]["Claude Sonnet 5"]["input_per_1k"] = 0.003
    d = diff(drifted, live)
    assert d == [("Claude Sonnet 5", "input_per_1k", 0.003, 0.002)], d; n += 1

    # A SKU that disappears refuses and names what the source does publish.
    try:
        fetch_live(base, _fix_azure([r for r in _FIX_ROWS if r["skuName"] != "5.4 opt Gl"]))
        raise AssertionError("missing SKU must refuse")
    except PriceUnavailable as e:
        assert "5.4 opt Gl" in str(e) and "SKUs returned" in str(e); n += 1

    # Two prices for one SKU refuse instead of picking one.
    try:
        fetch_live(base, _fix_azure(_FIX_ROWS + [_row("Azure OpenAI", "gpt 4.1 Inp glbl", 0.0022, "1K")]))
        raise AssertionError("ambiguous SKU must refuse")
    except PriceUnavailable as e:
        assert "Refusing to pick one" in str(e); n += 1

    # No network and no previous fetch: refuse, never revert to the hand table.
    def offline(_url):
        raise urllib.error.URLError("no network")
    missing = os.path.join(HERE, "__no_such_cache__.json")
    try:
        load_with_fallback(base, missing, offline)
        raise AssertionError("must refuse without network and cache")
    except PriceUnavailable as e:
        assert "no previous fetch" in str(e); n += 1

    print("self-test OK (%d asserts)" % n)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Resolve model prices from public sources.")
    ap.add_argument("--base", default=os.path.join(HERE, "prices.example.json"),
                    help="table declaring each model's source (default: prices.example.json)")
    ap.add_argument("--out", default=os.path.join(HERE, "prices.live.json"))
    ap.add_argument("--check", action="store_true",
                    help="compare the base table against live; exit 1 on any drift")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    with open(a.base, encoding="utf-8") as fh:
        base = json.load(fh)
    live, notes = load_with_fallback(base, a.out)
    for note in notes:
        print("NOTE  " + note)
    print("prices: %s (%s)\n" % (live["provenance"], live["pricing_as_of"]))
    print(render(live))
    print("\nUSD per million tokens. Sources:")
    for name, src in live.get("sources", {}).items():
        print("  %-10s %s" % (name, " | ".join(src) if isinstance(src, list) else src))
    if not a.check:
        print("\nwritten to %s" % a.out)
        return 0
    drift = diff(base, live)
    if not drift:
        print("\nthe hand table matches every source it declares.")
        return 0
    print("\nDRIFT: the hand table disagrees with the source")
    for name, field, mine, theirs in drift:
        print("  %-18s %-20s table %-10s source %s" % (name, field, mine, theirs))
    return 1


if __name__ == "__main__":
    sys.exit(main())
