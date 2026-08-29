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
    """The strings whose presence confirms a candidate, longest first.

    For a query written without spaces -- most Japanese -- the whole query is
    one needle, and confirmation is a phrase match.

    For a space-separated query it is every contiguous run of **two or more**
    words, longest first. Single words are needles only when the query is a
    single word.

    That floor is the point. A document sharing one common word with a question
    is not thereby about it: "how many nodes does the staging cluster have"
    matched "The node count of the build farm" on the word *nodes* alone, which
    let a document about something else into a package. A phrase is evidence
    that a document is about the query; a token is evidence that it is written
    in the same language.

    Measured, not guessed. On the evaluation corpus the lexical-near-miss trap
    rate went 96.7% -> 36.7% (keeping unconfirmed candidates out of packages)
    -> 10.0% (this change), and train and held-out agree at 10%, so it is not
    fitted to the cases it was measured on.

    **Punctuation at a boundary is not content.** A query is a question and
    people type it as one: ``テントの重量は?`` was confirmed against nothing,
    because the whole spaceless query is one needle and no document contains
    the question mark. The README's own example returned an empty package. So
    leading and trailing punctuation is trimmed, from the query and from each
    word -- a boundary trim, not a tokenization change, and not a stopword
    list. Internal punctuation stays: ``don't`` is one word and ``config.yaml``
    is one name.

    **The residual 10% is diagnosed, not mysterious**, and is left alone. All
    three remaining failures confirm on a stopword phrase: "when is the first
    ferry departure" matches a document about a shuttle bus on *the first*.
    Fixing that needs term rarity -- which the index has, as bm25, and this
    stage does not -- or a stopword list, which is a vocabulary list per
    language and does not generalise. Chasing it on thirty synthetic cases
    would be fitting the ranker to the fixtures.
    """
    folded = _trim_punctuation(unicodedata.normalize("NFKC", query).casefold())
    words = [trimmed for w in folded.split() if (trimmed := _trim_punctuation(w))]
    if len(words) <= 1:
        return [words[0]] if words else ([folded] if folded else [])

    runs: list[str] = []
    for length in range(len(words), 1, -1):
        for start in range(len(words) - length + 1):
            runs.append(" ".join(words[start : start + length]))
    return list(dict.fromkeys(runs))


def _trim_punctuation(text: str) -> str:
    """Drop punctuation and symbols from both ends.

    By Unicode category rather than by a list of characters, so ``?``, ``？``,
    ``。``, ``।`` and ``؟`` are all covered without anyone having to think of
    them. Nothing is removed from the middle: a needle is still a phrase.
    """
    start, end = 0, len(text)
    while start < end and unicodedata.category(text[start])[0] in {"P", "S"}:
        start += 1
    while end > start and unicodedata.category(text[end - 1])[0] in {"P", "S"}:
        end -= 1
    return text[start:end]


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
