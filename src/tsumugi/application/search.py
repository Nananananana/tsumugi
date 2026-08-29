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
from typing import Final

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
    terms = _content_terms(query)
    results: list[SearchResult] = []

    for hit in candidates:
        document = store.get(hit.document_id, hit.version) or store.get(hit.document_id)
        if document is None:
            # The index is derived and can outlive a document. Not an error;
            # `tsumugi doctor` is where drift gets reported.
            continue

        spans = _confirm(document.content, needles) or _confirm_by_coverage(document.content, terms)
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


#: How much of a question's content has to be present before a candidate
#: confirms on coverage. At 1.0 the rule reads: **every content term of the
#: question appears in the candidate.**
#:
#: Measured, and the measurement is the reason it sits here rather than lower.
#: On the evaluation corpus every value from 0.8 to 1.0 scores identically --
#: train recall 95.6% / precision 98.8% / traps 5.7%, held-out 80% / 100% /
#: 6.7%, which is train and held-out agreeing rather than a number fitted to
#: its own cases. Below 0.8 the trap rate climbs fast: 0.7 doubles it, 0.5
#: takes it to 28.6% on train and 40% held-out.
#:
#: The corpus cannot separate 0.8 from 1.0, so this is chosen rather than
#: measured, and it is chosen strict. Where evidence is absent this library
#: fails closed. Lowering it wants a corpus with compound terms that are
#: partially shared -- ``集合場所`` asked of a document that says ``集合`` --
#: which this one does not have.
COVERAGE_THRESHOLD: Final = 1.0

#: Unicode blocks whose runs carry the question's content. Hiragana is left out
#: on purpose: in Japanese it is particles and inflection, which is exactly the
#: material that changes when the same question is asked differently.
_CONTENT_SCRIPTS: Final = frozenset({"Han", "Katakana", "Latin", "Digit"})


def _script_of(character: str) -> str:
    """A coarse script class, from the code point.

    Coarse deliberately. This is not a tokenizer and must not become one: the
    only question is which characters carry content and which are grammar.
    """
    code = ord(character)
    if 0x3040 <= code <= 0x309F:
        return "Hiragana"
    if 0x30A0 <= code <= 0x30FF or 0xFF66 <= code <= 0xFF9D:
        return "Katakana"
    if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
        return "Han"
    if character.isdigit():
        return "Digit"
    if character.isalpha():
        return "Latin"
    return "Other"


def _content_terms(query: str) -> list[str]:
    """The parts of a question that carry its subject, as script runs.

    ``テントの重量は`` -> ``[テント, 重量]``. The ``の`` and ``は`` are dropped,
    which is the point: they are what changes when somebody asks the same
    thing in different words.

    No morphology and no dictionary. A run is a run of one script, which is
    structure the string already has -- and a rule that needed a word list
    would need one per language and would not survive the next corpus.
    """
    folded = unicodedata.normalize("NFKC", query).casefold()
    terms: list[str] = []
    current, script = "", ""
    for character in folded:
        found = _script_of(character)
        if found == script and found in _CONTENT_SCRIPTS:
            current += character
            continue
        if current:
            terms.append(current)
        current = character if found in _CONTENT_SCRIPTS else ""
        script = found
    if current:
        terms.append(current)
    return terms


def _longest_present(term: str, folded: str) -> tuple[int, str]:
    """The longest substring of ``term`` that occurs in ``folded``.

    Substrings rather than the whole term because a compound is a compound:
    ``集合場所`` asked of a document that says ``集合`` should count what it
    shares, not nothing. Two characters is the floor for a multi-character
    term -- a single character of a compound is a coincidence.
    """
    floor = 1 if len(term) == 1 else 2
    for length in range(len(term), floor - 1, -1):
        for start in range(len(term) - length + 1):
            piece = term[start : start + length]
            at = folded.find(piece)
            if at != -1:
                return at, piece
    return -1, ""


def _confirm_by_coverage(content: str, terms: Sequence[str]) -> list[Span]:
    """Confirm when enough of the question's content is present.

    **A fallback, never a replacement.** It runs only where the phrase rule
    found nothing -- which today means the candidate is rejected outright -- so
    it can turn a rejection into a result and never the other way round. That
    is what keeps ADR-0007's guarantee intact: the index still over-generates
    and confirmation still decides.
    """
    total = sum(len(term) for term in terms)
    if not total:
        return []

    folded = unicodedata.normalize("NFKC", content).casefold()
    matched, spans = 0, []
    for term in terms:
        at, piece = _longest_present(term, folded)
        if at == -1:
            continue
        matched += len(piece)
        spans.append(Span(min(at, len(content)), min(at + len(piece), len(content))))

    if matched / total < COVERAGE_THRESHOLD:
        return []
    return sorted(spans, key=lambda span: span.start)


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


#: Characters that end a sentence outright. CJK punctuation is unambiguous:
#: nothing else uses these, so no lookahead is needed.
_HARD_STOPS: Final = "。．！？!?"

#: A full stop ends a sentence in Latin script only when something follows it
#: that is not more sentence. ``2.4kg`` and ``e.g.`` are not sentence ends, and
#: a rule that thought they were would cut an item in half.
_SOFT_STOP: Final = "."


def _sentence_start(content: str, floor: int, at: int) -> int:
    """The start of the sentence containing ``at``, no earlier than ``floor``."""
    for index in range(at - 1, floor - 1, -1):
        character = content[index]
        if character in _HARD_STOPS:
            return index + 1
        if character == _SOFT_STOP and index + 1 < len(content) and content[index + 1].isspace():
            return index + 1
    return floor


def _sentence_end(content: str, at: int, ceiling: int) -> int:
    """The end of the sentence containing ``at``, no later than ``ceiling``."""
    for index in range(at, ceiling):
        character = content[index]
        if character in _HARD_STOPS:
            return index + 1
        if character == _SOFT_STOP and (index + 1 >= len(content) or content[index + 1].isspace()):
            return index + 1
    return ceiling


def _widen(content: str, span: Span, context: int) -> Span:
    """Grow a match outwards to a sentence boundary.

    A bare match is unreadable and unusable as context, so an item carries the
    sentence around it. The boundary is whichever comes first: a line break, a
    sentence terminator, or the context limit.

    **It used to stop only at a line break**, while this docstring already said
    "sentence-ish". On a corpus where every fact sits on its own line the two
    are the same, which is why nobody noticed -- until the evaluation corpus
    started planting facts mid-paragraph and an item that should have cost
    seventeen characters cost sixty, lost a tight budget, and took a case with
    it. Prose is the normal case for a notes folder; one sentence per line is
    the fixture.
    """
    floor = max(0, span.start - context)
    ceiling = min(len(content), span.end + context)

    line_start = content.rfind("\n", floor, span.start)
    start = max(
        floor if line_start == -1 else line_start + 1, _sentence_start(content, floor, span.start)
    )

    line_end = content.find("\n", span.end, ceiling)
    end = min(ceiling if line_end == -1 else line_end, _sentence_end(content, span.end, ceiling))

    return Span(start, max(end, span.end))


def _section_name(document: Document, offset: int) -> str:
    section: Section | None = document.section_at(offset)
    return section.heading if section is not None else ""
