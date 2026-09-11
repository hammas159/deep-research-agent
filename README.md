# deep-research-agent

[![ci](https://github.com/hammas159/deep-research-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/hammas159/deep-research-agent/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![dependencies](https://img.shields.io/badge/core-none-success)
![license](https://img.shields.io/badge/license-MIT-green)

**A research agent that tells you when the sources disagree, instead of picking one.**

`plan → search → fetch → deduplicate → corroborate → verify → report`, under a budget it
cannot reason its way past. Core has zero dependencies; search and fetch are injected, so
the whole thing is testable with no network and no model.

---

## Three things most research agents get wrong

### 1. Ten sources is not ten sources

A wire story is republished verbatim by dozens of outlets. An agent that counts URLs
reports *"corroborated by 12 sources"* about a claim with exactly one origin — and the
confidence it reports is **fabricated**.

Near-duplicates are collapsed *before* anything is counted, using hashed word-shingles,
and the unit of corroboration is an independent source:

```
5 documents fetched  →  3 independent sources  (independence ratio 0.6)
reuters.com ........... copies_found: 3   ← dawn.com and a blogspot mirror
```

The **highest-authority** copy represents its group, not the first one seen. If a wire
story appears on both the agency and an aggregator, the agency is what gets cited — the
order results arrived in is not evidence about anything.

Claims are extracted from the representative only. Extracting from every copy would let
one story contribute the same claim a dozen times and manufacture exactly the
corroboration deduplication just removed. That's a test.

### 2. Disagreement is the finding, not a problem to resolve

A typical agent feeds every document to a model and asks for a summary. When the
documents conflict, the model silently picks one — and the conflict, which was the most
useful thing the research turned up, disappears.

```python
report.disputed
# [{"subject": "Inflation in Pakistan",
#   "values": [8.2, 12.4], "spread": 0.41, "confidence": "disputed",
#   "citations": [{"source": "reuters.com/a",    "quote": "...was 8.2 percent..."},
#                 {"source": "worldbank.org/d",  "quote": "...was 12.4 percent..."}]}]
```

Agreement is separated from dispute in the report, and confidence comes from
**corroboration count, not tone**:

| | |
|---|---|
| `disputed` | sources conflict — capped regardless of how many are on each side |
| `well-corroborated` | 3+ independent sources |
| `corroborated` | 2 |
| `single-source` | 1 |

Ordinary measurement variation is *not* a contradiction — 8.2% and 8.4% corroborate each
other; 8.2% and 12.4% do not. The tolerance is explicit and tested both ways.

Consensus is the **median**, and on an even count `statistics.median` averages the middle
pair. Taking `ordered[n // 2]` would silently prefer the higher of two disagreeing
sources — a bias dressed up as a tie-break. That was a real bug here.

### 3. Every quote is verified against the fetched text

```python
def test_a_fabricated_quote_is_rejected():
    """A citation the agent wrote from memory is worse than no citation,
    because it looks like evidence."""
```

Whitespace-insensitive, otherwise **exact**. A near-miss is a miss — a fuzzy threshold is
precisely where fabricated quotes slip through. Dropped quotes are counted in the report
rather than hidden.

## The budget is outside the loop

Research is unbounded by nature: there is always one more source. So the stopping
condition comes from somewhere the agent cannot influence — queries, fetches, wall clock.

It also **stops when it has enough**. Research that can't stop once the question is
answered is as broken as research that can't stop at all:

```
"stopped_because": "found enough corroborated evidence"
"stopped_because": "fetches budget exhausted: 20 of 20"
```

And planning always includes a **contrary query**. An agent that only searches for
confirmation will find it.

## Usage

```python
agent = ResearchAgent(search=my_search, fetch=my_fetch, min_authority=0.6)
report = agent.run("What is inflation in Pakistan?")

report.summary()
# {"findings": 2, "disputed": 1,
#  "independent_sources": 3, "documents_fetched": 5, "independence_ratio": 0.6,
#  "unverified_quotes_dropped": 0,
#  "stopped_because": "found enough corroborated evidence"}
```

`search(query, limit) -> [url]` and `fetch(url) -> Source` are yours. Plug in Tavily,
Brave, SerpAPI or a local index — the agent does not know or care, which is why it can be
tested exhaustively without one.

## Source authority

A coarse prior on reliability, from the domain: primary research → official → established
press → reference → commercial → user-generated. Deliberately coarse, because a finer
taxonomy would imply precision this heuristic does not have.

**It is a weight on evidence, never a substitute for checking it.** A high-authority
source saying something no other source says is still a single unverified claim, and the
report says so.

## Tests

**48 tests. No network, no API key, no model.**

| Covered | |
|---|---|
| Authority | five domain classes, research outranks blogs, unknown domains untrusted |
| Deduplication | copies collapse, highest-authority representative, distinct docs stay apart, independence ratio, shingling edges |
| Extraction | numeric claims, unit scaling (2 billion = 2000 million), comma parsing, negation, whole-sentence quotes |
| Corroboration | agreement, dispute, measurement variation ≠ contradiction, **median consensus**, opposing stances, disputes sort first |
| Planning | contrary query always present, quantitative and comparative shapes, dedup and cap |
| Verification | exact match, whitespace tolerance, **fabricated quote rejected**, near-miss is a miss |
| Agent | agreement/dispute separated, **copies do not corroborate**, query and fetch budgets, early stop, dead links, authority floor, empty results |

## Limits

- **Claim extraction is pattern-based.** It catches numeric and simple assertional
  claims and little else. That is a real ceiling on recall — but it is deterministic,
  inspectable, and it means the corroboration logic is verifiable without an API key. A
  model-based extractor fits behind the same `Claim` interface.
- Contradiction detection is numeric and stance-based. Two sources disagreeing about
  *causation* in prose will not be caught.
- Deduplication is lexical. A story rewritten in different words reads as independent
  when it is not — the hardest case, and it needs semantics.
- Domain authority is a heuristic, not a reputation system, and it encodes assumptions
  worth arguing with.
- No synthesis prose. The report is structured findings with citations; turning that
  into readable narrative is a model's job, and it should be given *verified* findings
  rather than raw documents.

## License

MIT
