# Agent cost math

Price an agent architecture before you build it: from its structure, not its volume, against the vendors' own prices.

Companion code for the **Microsoft Ignite 2026** Table Talk **TT1759, "Agent Cost Is an Architecture Decision, Not a Token Count"** (Agent Platform, level 300).

Python 3.8 or newer and nothing else: stdlib only, no `pip install`, no API keys, no cloud account. Clone it and run it at the table. The only command that touches the network is the one that asks for live prices, and it says so.

Prefer the browser? The same engine runs on the session page, in English and Portuguese: <https://julianamarialop.github.io/en/agent-cost/>

| What | Entry point |
|---|---|
| Price an agent architecture from its own structure | `agent_cost.py --architecture` |
| The same, priced from the vendors' public sources right now | `agent_cost.py --architecture ... --live` |
| Is the hand price table still the vendor's price? | `live_prices.py --check` |
| Context inflation, as a formula and as a picture | `agent_cost.py --inflation` / `--trace` |
| Cascade routing break even | `agent_cost.py --cascade` |
| Map, reduce in code, synthesize | `agent_cost.py --mapreduce` |
| A whole agent in one file, to break on purpose | `tiny_agent.py` |

The repo also carries four engines on neighboring problems, documented further down: preparing data for a non human reader (`data_for_agents.py`), organizational context with declared precedence (`org_context.py`), sizing a data platform by inventory (`platform_sizing.py`) and proving a quality gate actually runs (`gate_lab.py`).

```bash
python agent_cost.py      --self-test   # 59 asserts
python live_prices.py     --self-test   # 30 asserts, offline, against recorded responses
python org_context.py     --self-test   # 33 asserts
python gate_lab.py        --self-test   # 28 asserts
python platform_sizing.py --self-test   # 23 asserts
python data_for_agents.py --self-test   # 16 asserts
python tiny_agent.py      --self-test   # 14 asserts
```

## Organizational context: three sources, one answer

"Which account should I prioritize this week?" needs three different kinds of context, and they are not interchangeable: **work signals** (who met whom, which thread went quiet), **certified business data** (revenue, churn risk, with grain and validity), and **organizational knowledge** (the prioritization policy, with authority and a date).

```bash
python org_context.py                       # answers, with its receipt
python org_context.py --missing knowledge   # refuses, and names what is missing
python org_context.py --user ae             # trims by identity, never names the omission
python org_context.py --no-precedence       # the same question, two different answers
```

The failure worth seeing: a two year old policy ranks accounts by revenue, a twelve day old directive ranks by churn risk. Both are legitimate. With no declared precedence, retrieval order decides:

```
retrieval order ['k1', 'k2'] -> Contoso   (annual revenue 3.4M against 1.2M)
retrieval order ['k2', 'k1'] -> Northwind (churn risk score 0.71 against 0.22)
   resolved by: retrieval order, because no precedence is declared
```

Same sources, same question, two answers, and nothing in either output signals the choice was arbitrary. A four entry precedence list ordered by **authority rather than recency** (`directive > policy > certified > signal`) makes the answer stop moving, and the test that proves it is one you can run today: reorder retrieval and assert the answer does not change.

Three more rules the code enforces rather than suggests:

- **A missing required source is a refusal.** A partial answer is indistinguishable from a complete one on the way out.
- **A resolved conflict is still reported.** Suppressing it makes a decision on the reader's behalf and hides a contradiction their organization has not noticed.
- **Permission is the user's, not the agent's**, resolved at query time, and the withheld item is never named. Saying "there is a report you cannot see" leaks the report.

## Your data was built for people

Three tables all plausibly answer "how many active customers do we have". On column names alone, an agent lands on the **right table and the wrong column**: it picks `customer_key` over `is_active`, because the key overlaps the question more. The dimension is SCD2, so counting the key without `valid_to IS NULL` counts every historical version of every customer, and the number comes out high, credible and unreviewable.

```bash
python data_for_agents.py
python data_for_agents.py --question revenue   # refuses: nobody defined the term
```

Four fields fix it, and none of them is a product: **meaning** (a column named before a schema change lies), **grain** (one row per customer, per validity window, or per event), **certification** (so a staging table stops outranking a mart), **restriction** (so the agent can be told to refuse a tax identifier).

## Size the platform by inventory, not by volume

Volume answers how much data moves. The bill is set by how many clusters run at the same time in the peak window.

```bash
python platform_sizing.py
#   sized by volume     4,149 node hours
#   sized by inventory 24,416 node hours  (5.9x under)
```

The two blocks a throughput calculation can never see: always on streaming and CDC at 5,760 node hours a month, which is **larger than the entire month end regulatory batch** at 2,880. Production is 22,016 of the 24,416, with the lower environments declared rather than assumed as a percentage.

With no inventory it refuses: there is no compute estimate, only a throughput floor. Given a higher external reference it anchors on that and says why, because undercutting the client architect's number silently is how a proposal wins on price and loses in delivery.

## Your gate is green because it never ran

Four ways a data quality gate stays green and dead, all four taken from defects I shipped:

```bash
python gate_lab.py --mutation            # correct gate: 6/6, alive
python gate_lab.py --broken --mutation   # plausible gate: 4/6, dead
python gate_lab.py --thin-manifest       # orphan rule, never runs
```

- **NEVER_RAN.** The rule reads `id` while the column is `customer_id`. The typo alone would be loud; what makes it silent is the `if key` guard written to skip empty values. Field wrong, so the key is always None, so the guard is always false, so the rule reports clean forever. Two defensible decisions, one check that cannot fail.
- **ORPHAN.** Drop a rule from the manifest and it stops running while still passing its own unit test. The report stays busy with other findings, so nothing looks wrong.
- **FALSE_POSITIVE.** Case insensitive `TODO` against Portuguese text, where "todo" means "all". It failed correct documents at scale, and a gate that fails correct work is a gate the team switches off.
- **CRASHES.** The rule raises, and a traceback teaches people to rerun with the gate disabled rather than fix the data.

The only defense is a mutation harness in **both** directions: every rule must fail the broken input and pass the correct one. Coverage credited to the wrong rule is not coverage, which the broken gate demonstrates by flagging a status mutation through an unrelated rule's false positive.

## Start here: a whole agent, in one file

`tiny_agent.py` is a complete agent with tools, a loop, an iteration ceiling and history replay. The model is a stub, which is the point: swap it for a real API call and nothing else in the file changes, because the loop is your code.

```bash
python tiny_agent.py
#   turn 1: reads  1,000 tokens (billed   1,000) -> lookup_order
#   turn 2: reads  2,000 tokens (billed   3,000) -> lookup_policy
#   turn 3: reads  3,000 tokens (billed   6,000) -> final answer
#   billed 6,000 input tokens across 3 turns, against the 3,000 a flat
#   per turn estimate would predict: +100.0%.
```

Then break it on purpose, which is where the learning is:

```bash
python tiny_agent.py --max-turns 2    # the ceiling stops it with no answer
python tiny_agent.py --no-history     # it gets stuck on step two, forever
```

The second one matters most. Without history replay the agent cannot tell that it already has what it needs, so it calls the same tool until the ceiling saves it. It also reads a flat 500 tokens per turn, so the broken agent looks **cheaper and healthier per turn** while never answering anything.

The five decisions, all visible in one function:

1. **Orchestration pattern.** One agent, a router, a supervisor, a hierarchy.
2. **Role, and therefore model tier.** What this component is for.
3. **Iteration ceiling.** What stops the loop, and what happens when it does.
4. **Who carries the history.** Full replay, a compressor, or external state.
5. **Who validates.** Code, or another model call that hands back the cost you were avoiding.

## The headline

A five component claims triage architecture, priced from its own structure:

```
component              calls/mo  in/task  model               USD/mo      %
Intent triage            40,000      900  Claude Haiku 4.5     40.00   2.4%
Claim specialist          9,000   62,000  Claude Opus 5     1,440.00  85.4%
Document extractor        9,000    8,000  Claude Haiku 4.5     73.80   4.4%
History compressor        9,000   12,000  Claude Haiku 4.5    106.20   6.3%
Final decision review       900    5,000  Claude Opus 5        25.88   1.5%
TOTAL                                                       1,685.88
```

The router handles **four times more requests** than the specialist and costs **thirty six times less**. Business volume does not predict agent cost. Orchestration does.

```bash
python agent_cost.py --architecture architecture.example.json
```

## What the code proves

### 1. Context inflation is quadratic

An agent is stateless, so the orchestrator resends the whole history every cycle. Input does not grow linearly with the number of steps:

```
total = N x base + delta x N x (N - 1) / 2
```

```bash
python agent_cost.py --inflation 2000 500 10
# 10 steps bill 42,500 tokens where the naive count says 20,000: +112.5%

python agent_cost.py --inflation 2000 500 20
# 20 steps bill 135,000 tokens where the naive count says 40,000: +237.5%
```

Doubling the ceiling from 10 to 20 does not double the bill, it triples it. That is why `max_iterations` is an architecture decision with a price attached, not a tuning knob.

### 2. The role decides the tier, and the task can override it upward

Eleven roles, each with a required tier and a written reason. The task class says what the work demands regardless of position, and the **higher of the two wins**.

```bash
python agent_cost.py --roles
```

An operational worker that happens to do tool using agentic work does not become cheap just by sitting at the bottom of the hierarchy. And a history compressor stays cheap precisely because its reader is the next iteration of the agent and not a human: classify it as summarization for humans and it moves to mid tier, killing the saving it exists to produce.

### 3. A smaller window can be more accurate, not less

The intuitive fix for a pipeline that misses things is to send more context. On a document extraction workload it did the opposite: with the whole contract in the window, the model missed the addendum on page 38. Attention is not uniform across a long input.

Split it, let a cheap model copy out the spans in parallel (Map), consolidate them in **code** (Reduce), then let the capable model reason over the digest:

```bash
python agent_cost.py --mapreduce 40000 10
# Claude Sonnet 5 reads 40,000 tokens at 0.0850. Map on Claude Haiku 4.5 plus a
# code Reduce leaves 2,000 tokens for Claude Sonnet 5: 0.0590, which is 30.6%
# cheaper on a window 95.0% smaller.

python agent_cost.py --mapreduce 40000 10 \
  --synth-model "Claude Opus 5" --naive-model "Claude Opus 5"
# ... 65.9% cheaper
```

Window down 95%, bill down by a third against a mid tier reader and two thirds against a frontier one. On the workload that motivated this, accuracy went from 75% to 99%.

The first number used to be 50.2%. It moved when the price table was corrected: Sonnet 5 had been transcribed at 3/15 per million while the vendor charges 2/10, so the reader is only twice the mapper's input price instead of three times. The design did not change. The ratio did, and the ratio is what pays for the restructuring.

Two things this deliberately does not do. It does not let the Reduce phase be a model call, because that is how teams rebuild the cost they just removed. And it does not return an accuracy number: accuracy is measured on a workload, never computed, and a tool that pretends otherwise teaches you to trust a figure nobody measured.

The saving collapses when both phases run on the same tier. What drives it is the price ratio, exactly as in cascade routing below.

### 4. Cascade routing pays only above the price ratio

Everything hits the cheap model first, and only what fails deterministic validation escalates:

```
cost = T x c1 + T x (1 - p) x c2      beats direct when  p > c1 / c2
```

```bash
python agent_cost.py --cascade 1 5 0.60 100
# cascade 300.00 vs direct 500.00 (+40.0%): worth it. Break even at 20.0% success.

python agent_cost.py --cascade 1 5 0.02 100
# cascade 590.00 vs direct 500.00 (-18.0%): MORE EXPENSIVE than going direct.
```

What decides it is the price ratio of the two models you actually picked, never a fixed success percentage repeated from a blog post. Same success rate of 5% is worth it at a ratio of 1 to 50 and a loss at 1 to 2.

**Non negotiable condition:** the cheap model's answer has to be validatable by code (schema, linter, explicit admission of failure). Validating with another model hands back the cost you were avoiding.

### 5. It refuses instead of guessing

```
agentic component 'Claim specialist' has no max_iterations.
  Without a ceiling there is no estimate, only an open range.

component 'Intent triage' has no monthly_volume.
  Without it this line would enter the estimate at zero and disappear from the proposal.

unknown or missing orchestration_pattern 'swarm'.
  Valid: cascade, hierarchical, map_reduce, router, single_agent, supervisor_specialist
```

A zeroed line vanishes from a proposal and nobody notices the absence. That is worse than an error.

### 6. Caching is modeled whole, including the two holes

Both of the usual omissions push the estimate **down**, which is the direction that hurts:

- **Cache write is billed.** Writing the prefix costs more than reading input, so caching is not pure saving. The write is amortized over the reads it serves before the TTL expires, and that read count is a declared premise, not a fact.
- **The cacheable floor is real and not monotonic across model generations.** Below the floor the cache simply does not happen, and the failure is **silent in the API itself**: no error, just zero cache creation tokens.

The example architecture declares caching on its router and the engine reports that it will not happen, because the 900 token prefix sits under that model's 4,096 token floor. It also reports the requested hit rate and the effective one separately, since saying "40% cached" while billing 0% cached is the same silent lie this code exists to prevent.

## Prices

A hand transcribed price table is exactly what goes wrong quietly: the model name stays right while the number drifts, and a check that compares names cannot see it. This repo proved it on itself. The table it shipped with had **Claude Sonnet 5 at 3/15 per million against a vendor price of 2/10**, and **GPT-4.1 at 5/15 against an Azure price of 2/8**. Every self-test passed. Every estimate was wrong.

So prices are now read from the vendors' public sources, with no key and no account:

| Models | Source | What it gives |
|---|---|---|
| Claude | Anthropic pricing page (markdown) | input, output, cache write, cache read |
| Claude | Anthropic prompt caching page | the minimum cacheable prompt, per model |
| GPT on Azure | Azure Retail Prices API | one meter per SKU, Global Standard deployment |

```bash
python live_prices.py --check
# Claude Haiku 4.5   1   5   0.1   1.25  4,096
# Claude Sonnet 5    2  10   0.2   2.5   1,024
# Claude Opus 5      5  25   0.5   6.25    512
# GPT-4.1            2   8   0.5   2     1,024
# GPT-5.4          2.5  15   0.25  2.5   1,024
# the hand table matches every source it declares.   (exit 1 when it does not)

python agent_cost.py --architecture architecture.example.json --live
```

`prices.example.json` declares, for each model, **where its price comes from**: the row name on the vendor page, or the exact Azure SKU. Three rules the code enforces:

- **Exact names, never substrings.** The GPT-5.4 family on Azure also publishes priority processing at twice the price, batch at half, and long context at double. A substring match reads the wrong one and nothing complains.
- **Units are read, not assumed.** Azure publishes older models per 1K tokens and newer ones per 1M, in the same API.
- **No silent fallback.** Without network, `--live` uses the previous fetch and prints its age; with no previous fetch it refuses. It never quietly returns to the hand table.

A shared public API rate limits a room full of people running the same command. `live_prices.py` waits for the `Retry-After` the API asks for, says so, and gives up loudly after two and a half minutes instead of hanging.

The example architecture **declares its models**. Left undeclared, the engine picks the cheapest model of the required tier from whatever table it was given, so the answer would move every time a vendor moved a price: with today's prices the cheapest high tier model on this table is GPT-5.4, not Claude Opus 5. The tier tells you what is adequate, not what is cheapest within it.

Point the engine at your own negotiated table with `--prices`.

## What this does not do

Stated up front, because a cost model that hides its own boundaries teaches you to distrust the next one.

- **It prices the LLM vector only.** Orchestrator infrastructure and vector databases are a separate vector. Third party API calls made by your tools are another. Both are deliberately out of scope here.
- **It does not estimate quality.** The cost against accuracy trade off is a decision made with pilot data, not a number this engine can produce.
- **It does not estimate the cheap tier success rate.** It computes the break even and tells you which rate you need. Measuring it is the pilot's job.
- **It does not estimate latency.** That is a non functional requirement, not a cost.

## The metric that actually matters

Not cost per token. **Cost per completed business task**, compared against the human cost of the same task. An agent that resolves a ticket for 0.80 against an analyst at 15.00 has financial headroom even when consumption swings.

And two defenses that belong in the architecture rather than in the estimate: per user quotas with graceful degradation to the cheap tier, and circuit breakers on cost anomaly per transaction.

## License

MIT.
