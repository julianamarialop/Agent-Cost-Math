# Agent cost math

An estimate of what an AI agent costs, derived from its architecture rather than its request volume, using the prices the vendors themselves publish.

Companion code for the **Microsoft Ignite 2026** Table Talk **TT1759, "Agent Cost Is an Architecture Decision, Not a Token Count"** (Agent Platform, level 300).

Requires only Python 3.8 or later. There are no packages to install, no API key and no cloud account. The only command that accesses the network is the one that retrieves current prices, and its output states that it did so.

The same calculator is available in the browser, in English and Portuguese, at <https://julianamarialop.github.io/en/agent-cost/>.

| Purpose | Command |
|---|---|
| Estimate the cost of an agent architecture | `agent_cost.py --architecture` |
| The same estimate, with prices retrieved from the vendors at execution time | `agent_cost.py --architecture ... --live` |
| Verify that the local price table still matches the vendors' prices | `live_prices.py --check` |
| Context growth across iterations, as a formula and as a chart | `agent_cost.py --inflation` / `--trace` |
| Break even point of cascade routing | `agent_cost.py --cascade` |
| Map, consolidation in code, synthesis | `agent_cost.py --mapreduce` |
| A complete agent in a single file, for experimentation | `tiny_agent.py` |

```bash
python agent_cost.py      --self-test   # 59 asserts
python live_prices.py     --self-test   # 30 asserts, offline, against recorded responses
python org_context.py     --self-test   # 33 asserts
python gate_lab.py        --self-test   # 28 asserts
python platform_sizing.py --self-test   # 23 asserts
python data_for_agents.py --self-test   # 16 asserts
python tiny_agent.py      --self-test   # 14 asserts
```

## The example

An online store's customer service assistant, answering the question "Where is my order?". Five components, estimated from the architecture:

```
component                 calls/mo  in/task  model               USD/mo      %
Question classification     40,000      900  Claude Haiku 4.5     40.00   2.4%
Order resolution agent       9,000   62,000  Claude Opus 5     1,440.00  85.4%
Shipping document reader     9,000    8,000  Claude Haiku 4.5     73.80   4.4%
History compressor           9,000   12,000  Claude Haiku 4.5    106.20   6.3%
Answer quality review          900    5,000  Claude Opus 5        25.88   1.5%
TOTAL                                                          1,685.88
```

The classification component receives **four times more requests** than the order resolution agent and costs **thirty six times less**. The cost of an agent is not determined by its volume of requests, but by its orchestration.

```bash
python agent_cost.py --architecture architecture.example.json
```

## What the calculation demonstrates

### 1. Context grows quadratically

The model retains no memory between iterations, so the orchestrator resends the full history on every iteration. Input volume does not grow linearly with the number of iterations:

```
total = N x base + delta x N x (N - 1) / 2
```

```bash
python agent_cost.py --inflation 2000 500 10
# 10 iterations consume 42,500 tokens, against 20,000 in the simple estimate: +112.5%

python agent_cost.py --inflation 2000 500 20
# 20 iterations consume 135,000 tokens, against 40,000 in the simple estimate: +237.5%
```

Raising the iteration limit from 10 to 20 triples the input volume. The iteration limit (`max_iterations`) is therefore an architecture decision with a direct effect on cost, and not an operational adjustment.

### 2. The role determines the model tier, and the task may raise it

The calculator defines eleven roles, each with a required tier and the reason for it. The task class states what the work requires regardless of the component's position, and **the higher of the two prevails**.

```bash
python agent_cost.py --roles
```

An operational component that performs tool based work does not qualify for a lower cost tier because of its position in the hierarchy. A history compressor remains on the lower cost tier because its output is read by the agent's next iteration and not by a person; if it were classified as a summary for human readers, it would move to the intermediate tier and the saving it exists to provide would be lost.

### 3. A smaller context can increase accuracy

The usual response to a process that misses information is to send more context. In a document extraction workload, the effect was the opposite: with the full contract in the context, the model missed an addendum on page 38, because attention is not uniform across a long input.

The alternative divides the document, has a lower cost model extract the relevant passages in parallel (Map), consolidates them **in code** (Reduce) and lets the higher capability model reason over the consolidated content:

```bash
python agent_cost.py --mapreduce 40000 10
# Claude Sonnet 5 reads 40,000 tokens at 0.0850. Map on Claude Haiku 4.5 plus a
# code Reduce leaves 2,000 tokens for Claude Sonnet 5: 0.0590, which is 30.6%
# cheaper on a window 95.0% smaller.

python agent_cost.py --mapreduce 40000 10 \
  --synth-model "Claude Opus 5" --naive-model "Claude Opus 5"
# ... 65.9% cheaper
```

The context is reduced by 95%, and the cost by one third with an intermediate tier model and by two thirds with a higher tier model. In the workload that motivated this approach, a single field measurement, accuracy rose from 75% to 99%.

The first figure was previously 50.2%. It changed when the price table was corrected: Claude Sonnet 5 had been recorded at USD 3/15 per million tokens while the vendor charges USD 2/10, so the ratio between the two models fell from three to two. The design did not change; the price ratio did, and the price ratio is what determines the saving.

Two deliberate limits apply. The consolidation step is never a model call, because that would reintroduce the cost the approach removes. And the calculator returns no accuracy figure: accuracy is measured on a real workload and is never computed.

### 4. Cascade routing reduces cost only above the price ratio

Every request is processed first by the lower cost model, and only those that fail deterministic validation are forwarded to the higher cost model:

```
cost = T x c1 + T x (1 - p) x c2      lower than direct when  p > c1 / c2
```

```bash
python agent_cost.py --cascade 1 5 0.60 100
# cascade 300.00 vs direct 500.00 (+40.0%): worth it. Break even at 20.0% success.

python agent_cost.py --cascade 1 5 0.02 100
# cascade 590.00 vs direct 500.00 (-18.0%): MORE EXPENSIVE than going direct.
```

The decision depends on the price ratio between the two models actually selected, and not on a fixed resolution rate. The same 5% resolution rate reduces cost at a ratio of 1 to 50 and increases it at a ratio of 1 to 2.

**Mandatory condition:** the lower cost model's answer must be validated by code (schema, linter or an explicit statement of failure). Validation by another model reintroduces the cost the arrangement is meant to avoid.

### 5. Missing inputs are refused, not assumed

```
agentic component 'Order resolution agent' has no max_iterations.
  Without a ceiling there is no estimate, only an open range.

component 'Question classification' has no monthly_volume.
  Without it this line would enter the estimate at zero and disappear from the proposal.

unknown or missing orchestration_pattern 'swarm'.
  Valid: cascade, hierarchical, map_reduce, router, single_agent, supervisor_specialist
```

A component estimated at zero disappears from a proposal without the omission being noticed, which is a more serious outcome than an error message.

### 6. Caching is modeled completely

The two usual omissions both understate the estimate:

- **Writing to the cache is charged.** Writing the prefix costs more than reading input, so caching does not produce a saving without conditions. The write cost is distributed over the reads it serves before expiry, and that number of reads is a declared premise.
- **Each model has a minimum cacheable prompt length, and it does not decrease with the model's size.** Below that minimum, caching does not take effect, and the API returns neither an error nor a warning.

The example declares caching on the classification component, and the calculator reports that it will not take effect, because the 900 token prompt is below that model's minimum of 4,096 tokens. The requested and effective cache rates are reported separately.

## Prices

A manually transcribed price table can diverge from the vendor's price without any visible sign: the model name remains correct while the price changes. This repository's own table recorded **Claude Sonnet 5 at USD 3/15 per million tokens against a vendor price of USD 2/10**, and **GPT-4.1 at USD 5/15 against an Azure price of USD 2/8**, while every self-test passed.

Prices are therefore retrieved from the vendors' public sources, without a key or an account:

| Models | Source | Information retrieved |
|---|---|---|
| Claude | Anthropic pricing page | input, output, cache write, cache read |
| Claude | Anthropic prompt caching page | minimum cacheable prompt length, per model |
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

`prices.example.json` declares the source of each model's price: the row name on the vendor's page, or the exact Azure SKU. The code enforces three rules:

- **Exact names, never partial matches.** The GPT-5.4 family on Azure also publishes priority processing at twice the price, batch at half the price and long context at twice the price. A partial match would read the wrong price without any error.
- **Units are read, not assumed.** Azure publishes earlier models per 1,000 tokens and recent ones per 1,000,000 tokens, in the same API.
- **No silent fallback.** Without network access, `--live` uses the previous retrieval and reports its age; without a previous retrieval, it declines to proceed. It never reverts to the manual table without stating so.

When the Azure API limits the request rate, `live_prices.py` waits for the interval the API indicates (`Retry-After`), reports the wait, and stops with an error after two and a half minutes.

The example architecture **declares its models**. When a model is not declared, the calculator selects the lowest cost model of the required tier from the price table it receives, so the result would change whenever a vendor changed a price. At current prices, the lowest cost higher tier model in the table is GPT-5.4, not Claude Opus 5: the tier indicates which models are adequate, not which one costs least.

A negotiated price table can be supplied with `--prices`.

## Scope limits

- **Only language model consumption is estimated.** Orchestration infrastructure, vector databases and third party APIs called by tools are separate cost components, outside the scope of this calculator.
- **Quality is not estimated.** The balance between cost and accuracy is decided with pilot data.
- **The resolution rate of the lower cost model is not estimated.** The calculator determines the break even point and the rate required; measuring the actual rate is the purpose of the pilot.
- **Latency is not estimated.** It is a non functional requirement, not a cost.

## The metric that matters

The relevant metric is not the cost per token, but the **cost per completed business task**, compared with the cost of the same task performed by a person.

Two safeguards belong in the architecture rather than in the estimate: per user quotas with fallback to the lower cost tier, and circuit breakers triggered by cost anomalies per transaction.

## Other tools in this repository

### `tiny_agent.py`: a complete agent in one file

An agent with tools, an iteration loop, an iteration limit and history replay. The model is simulated, so that the loop itself can be examined: replacing the simulation with a real API call changes nothing else in the file.

```bash
python tiny_agent.py
#   turn 1: reads  1,000 tokens (billed   1,000) -> lookup_order
#   turn 2: reads  2,000 tokens (billed   3,000) -> lookup_policy
#   turn 3: reads  3,000 tokens (billed   6,000) -> final answer
#   billed 6,000 input tokens across 3 turns, against the 3,000 a flat
#   per turn estimate would predict: +100.0%.

python tiny_agent.py --max-turns 2    # the limit stops the agent before it answers
python tiny_agent.py --no-history     # without history, the agent repeats the same step
```

Without history replay, the agent cannot determine that it already holds the information it needs and repeats the same tool call until the iteration limit stops it. Because it processes a constant 500 tokens per iteration, the defective agent appears less costly per iteration while never producing an answer.

### `org_context.py`: organizational context with declared precedence

The question "Which account should be prioritized this week?" requires three kinds of context: **work signals**, **certified business data** and **organizational knowledge**. When two sources conflict and no precedence is declared, the order of retrieval determines the answer:

```
retrieval order ['k1', 'k2'] -> Contoso   (annual revenue 3.4M against 1.2M)
retrieval order ['k2', 'k1'] -> Northwind (churn risk score 0.71 against 0.22)
   resolved by: retrieval order, because no precedence is declared
```

A precedence ordered by authority (`directive > policy > certified > signal`) makes the answer independent of retrieval order. A missing required source results in a refusal, a resolved conflict is still reported, and permissions are those of the user, applied at query time.

### `data_for_agents.py`: data prepared for a non human reader

When only column names are available, an agent selects the correct table and the wrong column (`customer_key` instead of `is_active`) and, in a slowly changing dimension, counts every historical version of each customer. Four attributes prevent this: **meaning**, **grain**, **certification** and **restriction**.

### `platform_sizing.py`: platform sizing by inventory

Sizing a data platform by volume yields 4,149 node hours per month; sizing by the inventory of concurrent workloads yields 24,416, 5.9 times more. Continuous streaming and CDC alone account for 5,760 node hours per month, more than the entire month end regulatory batch.

### `gate_lab.py`: verifying that a quality gate executes

Four ways a data quality gate reports success without executing: a rule that reads the wrong field and is skipped by its own guard condition, a rule removed from the manifest, a false positive on correct text, and a rule that fails with an exception. The only reliable verification is a mutation harness in both directions: every rule must reject the defective input and accept the correct one.

## License

MIT.
