"""The research loop, under a budget it cannot influence.

    plan → search → fetch → extract → deduplicate → corroborate → verify → report

Three things make this different from asking a model to "research X".

**The budget is enforced outside the loop.** Research is unbounded by nature — there is
always one more source — so the stopping condition has to come from somewhere the agent
cannot reason its way past.

**Disagreement survives to the report.** Sources that conflict are reported as
conflicting. Most agents summarise the conflict away, and the conflict was the finding.

**Every quote is verified against the fetched text** before it appears in the report.
A citation the agent wrote from memory is worse than no citation, because it looks like
evidence.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .claims import Claim, Finding, corroborate, extract
from .sources import Source, SourceGroup, deduplicate, domain_authority, independence

# Injected, so the whole agent is testable without a network.
SearchFn = Callable[[str, int], Sequence[str]]        # (query, limit) -> urls
FetchFn = Callable[[str], Source]                     # url -> Source


class BudgetExhausted(RuntimeError):
    def __init__(self, kind: str, limit: float, used: float) -> None:
        self.kind = kind
        super().__init__(f"{kind} budget exhausted: {used:.4g} of {limit:.4g}")


@dataclass
class Budget:
    max_queries: int = 6
    max_fetches: int = 20
    max_seconds: float = 120.0
    # Stop early once the question is answered. Research that cannot stop when it has
    # enough is as broken as research that cannot stop at all.
    sufficient_findings: int = 5
    sufficient_independent_sources: int = 4

    queries: int = 0
    fetches: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def check(self) -> None:
        if self.queries >= self.max_queries:
            raise BudgetExhausted("queries", self.max_queries, self.queries)
        if self.fetches >= self.max_fetches:
            raise BudgetExhausted("fetches", self.max_fetches, self.fetches)
        if self.elapsed >= self.max_seconds:
            raise BudgetExhausted("time", self.max_seconds, self.elapsed)

    def remaining(self) -> dict:
        return {
            "queries": self.max_queries - self.queries,
            "fetches": self.max_fetches - self.fetches,
            "seconds": round(self.max_seconds - self.elapsed, 2),
        }


def plan(question: str, *, max_queries: int = 6) -> list[str]:
    """Decompose a question into search queries.

    Deliberately simple and deterministic. A model-written plan is better at unusual
    questions and makes the agent untestable without one; this covers the common shapes
    and leaves the interface open.
    """
    base = question.strip().rstrip("?")
    queries = [base]

    lowered = base.lower()
    if any(w in lowered for w in ("how many", "how much", "rate", "percent", "number")):
        queries += [f"{base} statistics", f"{base} official data"]
    if any(w in lowered for w in ("why", "cause", "because", "reason")):
        queries += [f"{base} evidence", f"{base} criticism"]
    if any(w in lowered for w in ("best", "should", "better", "compare")):
        queries += [f"{base} drawbacks", f"{base} alternatives"]

    # A contrary query, always. An agent that only searches for confirmation finds it.
    queries.append(f"{base} contradicting evidence")

    seen: list[str] = []
    for q in queries:
        if q not in seen:
            seen.append(q)
    return seen[:max_queries]


def verify_quote(quote: str, source_text: str) -> bool:
    """Is this quote actually in the source?

    Whitespace-insensitive, otherwise exact. A near-miss is treated as a miss: the
    point of verification is to catch text the agent produced rather than read, and a
    fuzzy threshold is exactly where fabricated quotes slip through.
    """
    return " ".join(quote.split()).lower() in " ".join(source_text.split()).lower()


@dataclass
class Report:
    question: str
    findings: list[dict] = field(default_factory=list)
    disputed: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    independence: dict = field(default_factory=dict)
    stopped_because: str = ""
    queries_run: list[str] = field(default_factory=list)
    unverified_quotes: int = 0
    elapsed_seconds: float = 0.0

    def summary(self) -> dict:
        return {
            "question": self.question,
            "findings": len(self.findings),
            "disputed": len(self.disputed),
            "independent_sources": self.independence.get("independent_sources", 0),
            "documents_fetched": self.independence.get("documents", 0),
            "independence_ratio": self.independence.get("independence_ratio", 0.0),
            "unverified_quotes_dropped": self.unverified_quotes,
            "stopped_because": self.stopped_because,
        }


@dataclass
class ResearchAgent:
    search: SearchFn
    fetch: FetchFn
    budget_factory: Callable[[], Budget] = Budget
    duplicate_threshold: float = 0.6
    numeric_tolerance: float = 0.10
    min_authority: float = 0.0

    def run(self, question: str) -> Report:
        budget = self.budget_factory()
        report = Report(question=question)

        collected: list[Source] = []
        seen_urls: set[str] = set()
        stopped = "completed the plan"

        try:
            for query in plan(question, max_queries=budget.max_queries):
                budget.check()
                budget.queries += 1
                report.queries_run.append(query)

                for url in self.search(query, budget.max_fetches - budget.fetches):
                    if url in seen_urls:
                        continue
                    budget.check()
                    seen_urls.add(url)

                    authority, _ = domain_authority(url)
                    if authority < self.min_authority:
                        continue

                    budget.fetches += 1
                    try:
                        source = self.fetch(url)
                    except Exception:
                        # A dead link is ordinary. It costs its fetch and is skipped.
                        continue
                    if source.text.strip():
                        collected.append(source)

                if self._sufficient(collected, budget):
                    stopped = "found enough corroborated evidence"
                    break

        except BudgetExhausted as stop:
            stopped = str(stop)

        return self._assemble(question, collected, budget, stopped, report)

    def _sufficient(self, sources: list[Source], budget: Budget) -> bool:
        groups = deduplicate(sources, threshold=self.duplicate_threshold)
        if len(groups) < budget.sufficient_independent_sources:
            return False
        claims = self._claims(groups)
        findings = corroborate(claims, numeric_tolerance=self.numeric_tolerance)
        corroborated = [f for f in findings if f.independent_support >= 2]
        return len(corroborated) >= budget.sufficient_findings

    @staticmethod
    def _claims(groups: list[SourceGroup]) -> list[Claim]:
        """Claims are read from the representative only.

        Extracting from every copy would let one wire story contribute the same claim
        a dozen times and manufacture the corroboration the deduplication just removed.
        """
        claims: list[Claim] = []
        for group in groups:
            claims.extend(extract(group.representative.text, source_url=group.representative.url))
        return claims

    def _assemble(
        self, question: str, sources: list[Source], budget: Budget, stopped: str,
        report: Report,
    ) -> Report:
        groups = deduplicate(sources, threshold=self.duplicate_threshold)
        by_url = {g.representative.url: g for g in groups}

        claims = self._claims(groups)
        findings = corroborate(claims, numeric_tolerance=self.numeric_tolerance)

        verified: list[Finding] = []
        dropped = 0
        for finding in findings:
            kept = []
            for claim in finding.claims:
                group = by_url.get(claim.source_url)
                if group and verify_quote(claim.quote, group.representative.text):
                    kept.append(claim)
                else:
                    dropped += 1
            if kept:
                finding.claims = kept
                finding.supporting_sources = {c.source_url for c in kept}
                verified.append(finding)

        report.findings = [f.summary() for f in verified if not f.contradiction]
        report.disputed = [f.summary() for f in verified if f.contradiction]
        report.sources = [
            {
                "url": g.representative.url,
                "domain": g.representative.domain,
                "authority": g.authority,
                "class": g.authority_class,
                "copies_found": g.copies,
            }
            for g in sorted(groups, key=lambda g: -g.authority)
        ]
        report.independence = independence(groups)
        report.stopped_because = stopped
        report.unverified_quotes = dropped
        report.elapsed_seconds = round(budget.elapsed, 3)
        return report
