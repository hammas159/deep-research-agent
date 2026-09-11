"""Sources: quality, independence, and the difference between them.

Two ideas the rest of the agent depends on.

**Ten sources is not ten sources.** A wire story is republished verbatim by dozens of
outlets. A research agent that counts them reports "corroborated by 12 sources" about a
claim that has exactly one origin, and the confidence it reports is fabricated. So
near-duplicates are collapsed *before* anything is counted, and the unit of
corroboration is an independent source, not a URL.

**Quality is a prior, not a verdict.** A peer-reviewed paper is more likely to be right
than an anonymous blog, and that is worth encoding — but it is a weight on the evidence,
never a substitute for checking it. A high-authority source that says something no other
source says is still a single unverified claim.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Source:
    url: str
    title: str = ""
    text: str = ""
    published: str = ""      # ISO date, when known
    fetched_at: float = 0.0

    @property
    def domain(self) -> str:
        host = urlparse(self.url).netloc.lower()
        return host[4:] if host.startswith("www.") else host


# Domain classes, coarse on purpose. A finer taxonomy would imply a precision this
# heuristic does not have.
_TIERS: list[tuple[float, str, tuple[str, ...]]] = [
    (1.00, "primary_research", (".edu", "arxiv.org", "nature.com", "science.org",
                                "pubmed.ncbi.nlm.nih.gov", "doi.org")),
    (0.90, "official", (".gov", ".gov.pk", "who.int", "worldbank.org", "imf.org",
                        "un.org")),
    (0.75, "established_press", ("reuters.com", "apnews.com", "bbc.co.uk", "ft.com",
                                 "economist.com", "dawn.com")),
    (0.60, "reference", ("wikipedia.org", "britannica.com")),
    (0.55, "industry", (".org",)),
    (0.35, "commercial", (".com", ".net", ".io")),
]

_LOW_SIGNAL = ("blogspot.", "wordpress.com", "medium.com", "substack.com",
               "quora.com", "reddit.com", "pinterest.")


def domain_authority(url: str) -> tuple[float, str]:
    """A prior on reliability, from the domain alone. Returns (score, class)."""
    host = Source(url=url).domain
    if not host:
        return 0.3, "unknown"

    for marker in _LOW_SIGNAL:
        if marker in host:
            return 0.25, "user_generated"

    for score, label, markers in _TIERS:
        if any(host.endswith(m) or m in host for m in markers):
            return score, label

    return 0.3, "unknown"


# --- near-duplicate detection -----------------------------------------------------

_WORD = re.compile(r"[a-z0-9']+")


def shingles(text: str, size: int = 5) -> set[str]:
    """Hashed word n-grams.

    Word-level rather than character-level, and hashed rather than stored: two articles
    that differ only in headline and byline share almost every 5-gram of body text, and
    hashing keeps the comparison cheap on long documents.
    """
    words = _WORD.findall(text.lower())
    if len(words) < size:
        return {hashlib.blake2b(" ".join(words).encode(), digest_size=8).hexdigest()} if words else set()
    return {
        hashlib.blake2b(" ".join(words[i : i + size]).encode(), digest_size=8).hexdigest()
        for i in range(len(words) - size + 1)
    }


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return round(intersection / (len(a) + len(b) - intersection), 6)


@dataclass
class SourceGroup:
    """One independent origin, and every copy of it that was found."""

    representative: Source
    duplicates: list[Source] = field(default_factory=list)
    authority: float = 0.0
    authority_class: str = ""

    @property
    def copies(self) -> int:
        return 1 + len(self.duplicates)

    @property
    def domains(self) -> set[str]:
        return {self.representative.domain} | {d.domain for d in self.duplicates}


def deduplicate(
    sources: list[Source], *, threshold: float = 0.6
) -> list[SourceGroup]:
    """Collapse republished copies into single independent sources.

    The representative of a group is its **highest-authority** member, not the first
    seen. If a wire story appears on both an agency site and an aggregator, the agency
    is the one worth citing — and the order results happened to arrive in is not
    evidence about anything.
    """
    groups: list[SourceGroup] = []
    profiles: list[set[str]] = []

    for source in sources:
        profile = shingles(source.text)
        placed = False

        for group, existing in zip(groups, profiles):
            if jaccard(profile, existing) >= threshold:
                incoming_authority, incoming_class = domain_authority(source.url)
                if incoming_authority > group.authority:
                    group.duplicates.append(group.representative)
                    group.representative = source
                    group.authority = incoming_authority
                    group.authority_class = incoming_class
                else:
                    group.duplicates.append(source)
                placed = True
                break

        if not placed:
            authority, authority_class = domain_authority(source.url)
            groups.append(SourceGroup(
                representative=source, authority=authority, authority_class=authority_class,
            ))
            profiles.append(profile)

    return groups


def independence(groups: list[SourceGroup]) -> dict:
    """How much genuinely independent evidence is present.

    Reported as a ratio so the difference between "12 sources" and "12 copies of one
    source" is visible rather than buried.
    """
    total_documents = sum(g.copies for g in groups)
    return {
        "documents": total_documents,
        "independent_sources": len(groups),
        "independence_ratio": (
            round(len(groups) / total_documents, 4) if total_documents else 0.0
        ),
        "distinct_domains": len({d for g in groups for d in g.domains}),
    }
