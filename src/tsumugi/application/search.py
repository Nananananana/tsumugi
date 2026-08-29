"""Search: candidates from the index, confirmed against the anchored text.

Two stages, and the split is the design (ADR-0007). The index over-generates
because character bigrams do not know where words end. Confirmation is an exact
comparison against the text the store holds, which costs one string search on
content that is loaded anyway.

Approximate retrieval confirmed by exact evidence is the shape of the whole
library, and it is why the index is allowed to be replaced by anything -- an
embedding store, a real morphological analyser -- without touching a guarantee.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.anchor import Anchor
from ..domain.document import Document, Section
from ..domain.span import Span
from ..ports.index import Index
from ..ports.store import DocumentStore

__all__ = ["SearchResult", "Truncation", "search"]


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One confirmed match, anchored."""

    anchor: Anchor
    text: str
    source_path: str
    score: float
    #: The innermost section the match sits in, when the document has any.
    section: str = ""
    #: ``True`` when the index proposed this document and no exact occurrence
    #: of any query term was found in it. Reported rather than dropped: it is
    #: how the over-generation of the index becomes visible instead of
    #: mysterious.
    unconfirmed: bool = False


@dataclass(frozen=True, slots=True)
class Truncation:
    """A cap that bound the search, so it can be reported (ADR-0005)."""

    limit: int
    stage: str

    def as_omission_reason(self) -> str:
        return f"the {self.stage} stage returned its cap of {self.limit} candidates"


def search(
    query: str,
    *,
    store: DocumentStore,
    index: Index,
    limit: int = 10,
    candidate_limit: int = 50,
    context: int = 120,
) -> tuple[list[SearchResult], Truncation | None]:
    """Find spans of the corpus that bear on ``query``.

    Returns the results and, when a cap bound the work, what it was. The
    caller is responsible for reporting the truncation: an index that returns
    exactly its limit has told you it may have had more, and swallowing that
    makes a partial search look like a complete one.
    """
    candidates = index.search(query, limit=candidate_limit)
    truncated = (
        Truncation(candidate_limit, "candidate retrieval")
        if len(candidates) >= candidate_limit
        else None
    )

    needles = _needles(query)
    results: list[SearchResult] = []

    for hit in candidates:
        document = store.get(hit.document_id, hit.version) or store.get(hit.document_id)
        if document is None:
            # The index is derived and can outlive a document. Not an error;
            # `tsumugi doctor` is where drift gets reported.
            continue

        spans = _confirm(document.content, needles)
        if not spans:
            results.append(
                SearchResult(
                    anchor=Anchor.into(document, Span(0, min(context, len(document.content)))),
                    text=document.content[:context],
                    source_path=document.source_path,
                    score=hit.score,
                    section=_section_name(document, 0),
                    unconfirmed=True,
                )
            )
            continue

        best = spans[0]
        widened = _widen(document.content, best, context)
        results.append(
            SearchResult(
                anchor=Anchor.into(document, widened),
                text=widened.slice(document.content),
                source_path=document.source_path,
                score=hit.score + len(spans) * 0.01,
                section=_section_name(document, best.start),
            )
        )

    # Ties break on the anchor, never on iteration order: a package has to be
    # reproducible (ADR-0003).
    results.sort(key=lambda r: (-r.score, r.source_path, r.anchor.span.start))
    return results[:limit], truncated


def _needles(query: str) -> list[str]:
    """The strings whose presence confirms a candidate.

    Whitespace-separated words, plus the whole query. Normalized and
    case-folded so that a full-width or differently-cased occurrence still
    confirms -- the same normalization the index used.
    """
    folded = unicodedata.normalize("NFKC", query).casefold()
    parts = [p for p in folded.split() if p]
    return list(dict.fromkeys([folded, *parts])) if len(parts) > 1 else parts or [folded]


def _confirm(content: str, needles: Sequence[str]) -> list[Span]:
    """Every exact occurrence of a needle, longest needles first."""
    folded = unicodedata.normalize("NFKC", content).casefold()
    # Per-character normalization keeps lengths aligned for the common case;
    # where it does not, the span is still inside the document and the anchor
    # records what it actually covers.
    found: list[Span] = []
    for needle in sorted(needles, key=len, reverse=True):
        start = folded.find(needle)
        while start != -1 and len(found) < 64:
            end = min(start + len(needle), len(content))
            found.append(Span(min(start, len(content)), end))
            start = folded.find(needle, start + 1)
        if found:
            break
    return found


def _widen(content: str, span: Span, context: int) -> Span:
    """Grow a match outwards to a sentence-ish boundary.

    A bare match is unreadable and unusable as context. Widening stops at a
    line break where there is one nearby, and at a character count otherwise.
    """
    start = max(0, span.start - context)
    end = min(len(content), span.end + context)
    line_start = content.rfind("\n", start, span.start)
    if line_start != -1:
        start = line_start + 1
    line_end = content.find("\n", span.end, end)
    if line_end != -1:
        end = line_end
    return Span(start, max(end, span.end))


def _section_name(document: Document, offset: int) -> str:
    section: Section | None = document.section_at(offset)
    return section.heading if section is not None else ""
