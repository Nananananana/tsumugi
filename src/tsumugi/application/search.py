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
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
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
    #: How many characters of the query confirmed here. Zero when the match
    #: came from coverage rather than a phrase.
    matched: int = 0


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
    confirmation: Confirmation | None = None,
) -> tuple[list[SearchResult], Truncation | None]:
    """Find spans of the corpus that bear on ``query``.

    ``confirmation`` carries the three numbers that decide whether a candidate
    is confirmed. They are a parameter rather than three constants because the
    sweep in ``tools/measure_sensitivity.py`` found all three sitting on
    cliffs -- between 13 and 17 points of recall or trap ride on each, and
    every one of them was chosen against this corpus.


    Returns the results and, when a cap bound the work, what it was. The
    caller is responsible for reporting the truncation: an index that returns
    exactly its limit has told you it may have had more, and swallowing that
    makes a partial search look like a complete one.
    """
    settings = confirmation or DEFAULT_CONFIRMATION
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

        # Confirmation happens inside the part the index actually scored. A
        # hit for one section of a long document should not be confirmed by a
        # phrase four sections away: that is how the right document comes back
        # with the window on the wrong occurrence, which is 68% of the recall
        # lost at realistic document sizes.
        #
        # Offsets come back into the parent, so anchors are unchanged.
        region = hit.span or Span(0, len(document.content))
        scoped = region.slice(document.content)

        spans, matched = _confirm(scoped, needles)
        if not spans:
            # Coverage confirms without a phrase, so there is no matched run to
            # score. It ranks on bm25 and occurrence count alone, which is the
            # right order of preference: a phrase is stronger evidence.
            spans, matched = _confirm_by_coverage(scoped, terms, settings), 0
        spans = [Span(s.start + region.start, s.end + region.start) for s in spans]
        if not spans:
            results.append(
                SearchResult(
                    anchor=Anchor.into(
                        document,
                        Span(region.start, min(region.start + context, region.end)),
                    ),
                    text=scoped[:context],
                    source_path=document.source_path,
                    score=hit.score,
                    # `region.start`, not 0. The confirmed path below names the
                    # section the match is in; this one named the section at
                    # the head of the document, so every unconfirmed hit in a
                    # multi-section file was labelled with the first heading it
                    # happened to have. `search` publishes this field, and a
                    # reader following it turns to the wrong page.
                    section=_section_name(document, region.start),
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
                # Two bonuses, and the second is the one that matters. How
                # *much* of the question was confirmed separates the answer
                # from a near-miss that shares everything but the subject;
                # how many times it occurred is a much weaker signal.
                score=(
                    hit.score
                    + len(spans) * 0.01
                    + (matched / len(query) if query else 0.0) * MATCH_WEIGHT
                ),
                section=_section_name(document, best.start),
                matched=matched,
            )
        )

    results = _apply_relative_floor(results, settings)

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
#: **Measured now, not chosen.** ADR-0018 recorded that the corpus could not
#: separate 0.8 from 1.0; it can, as of 2026-09-05, and it agrees -- 0.6 buys
#: 0.6 recall points for 13.3 trap points, and 0.8 costs 0.8. A setting rather
#: than a constant, because a value that swings the trap rate 13 points across
#: its range is fitted to one corpus: see `Confirmation` at the foot of this
#: file. It stays strict, so that where evidence is absent this library fails
#: closed. Lowering it wants a corpus with compound terms that are
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


#: How much of a term may be inflection. A term whose stem is present and whose
#: tail is no longer than this counts as present in full.
#:
#: Korean is what asked for it. ``가계부의`` and ``가계부`` are the same word
#: with a particle attached, and the particle is Hangul like the stem, so the
#: script segmentation that separates ``テント`` from ``の`` cannot separate
#: them. Every Korean case in the corpus failed until this existed. English
#: plurals and Japanese okurigana fall out of the same rule, and it needs no
#: word list -- which is the constraint that ruled out a segmenter in ADR-0007
#: and a stopword list in ADR-0018.
INFLECTION_TAIL: Final = 2


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


def _counted(term: str, piece: str, tail: int = INFLECTION_TAIL) -> int:
    """How much of ``term`` a match on ``piece`` is worth.

    A stem match counts as the whole term, so a language that glues its grammar
    to its nouns is not permanently below the coverage threshold. The stem has
    to be most of the word: a prefix, at least two characters, and no more than
    ``INFLECTION_TAIL`` short of the whole.
    """
    missing = len(term) - len(piece)
    if missing and (missing > tail or len(piece) < 2 or not term.startswith(piece)):
        return len(piece)
    return len(term)


#: How many occurrences of one term are considered when locating the evidence.
#: A term that appears more often than this in one document is a term whose
#: exact position stopped mattering.
_OCCURRENCE_CAP: Final = 32


def _occurrences(piece: str, folded: str) -> list[int]:
    """Every position of ``piece``, up to the cap."""
    found: list[int] = []
    at = folded.find(piece)
    while at != -1 and len(found) < _OCCURRENCE_CAP:
        found.append(at)
        at = folded.find(piece, at + 1)
    return found


def _apply_relative_floor(
    results: list[SearchResult], settings: Confirmation
) -> list[SearchResult]:
    """Demote a match that is weak beside the best evidence this query found.

    A document containing five words of a six-word question, where the missing
    word is the subject, is a document about something else -- and no absolute
    threshold can say that, because five words is a lot in one corpus and
    nothing in another (ADR-0019).

    **Beside the strongest match anywhere in this query's results**, and the
    alternative is measured rather than argued. Section indexing turned one
    document into several candidates and cost 0.7 trap points, so
    `proposals/0003` asked whether scoping this comparison to a document --
    *is this the right part of this document* -- would recover them.

    It does the opposite. Measured over the labelled corpus by
    `tools/measure_floor_scope.py`:

        scope      recall   precision    trap
        query       87.2%       98.2%    5.0%
        document    87.2%       97.0%   26.7%

    A weak section of a weak document clears its own document's low bar, so
    every document that matched anything at all contributes its best part as
    evidence. The variant lives in that tool rather than as a setting here: a
    switch whose only measurement says it is five times worse is a switch
    somebody will turn on.
    """
    strongest = max((result.matched for result in results), default=0)
    if not strongest:
        return results
    floor = strongest * settings.relative_match_floor
    return [
        result
        if result.unconfirmed or result.matched >= floor or not result.matched
        else replace(result, unconfirmed=True)
        for result in results
    ]


#: The map from a folded index back to the source index it came from.
#:
#: ``None`` is the **identity** -- the fold produced one character for one
#: character, so folded index *i* came from source index *i* and there is
#: nothing to store. That is not a rare case optimised for its own sake: every
#: corpus of English or of source code folds this way, and so does every one of
#: the 780 documents in this project's evaluation corpus. It is the same fact
#: `tools/mutate.py` cites as the reason a wrong offset map once shipped
#: unnoticed, which is worth stating twice: **it makes the cost free and the
#: tests blind, and only one of those is good news.**
#:
#: Otherwise a read-only `memoryview` of unsigned 4-byte entries. Read-only
#: because the value is cached and handed to every caller that folds the same
#: document; four bytes because a Python integer in a tuple is thirty-six.
Origins = memoryview | None


def _fold_with_origins(content: str) -> tuple[str, Origins]:
    """NFKC-casefold ``content``, and say where each folded character came from.

    **Matching happens in folded space and anchors live in original space, and
    those two are not the same length.** NFKC turns one character into three
    (``㈱`` -> ``(株)``, ``½`` -> ``1/2``), three into one (halfwidth ``ｶ`` plus
    a voiced mark -> ``ガ``), and one into two (the ``ﬁ`` ligature, which is
    what PDF extraction produces). Every one of those shifts every offset after
    it.

    Before this existed, a span found in folded space was applied directly to
    the original: a document beginning ``ｶﾞｲﾄﾞ:`` anchored ``テントは`` two
    characters early and returned ``': テ'``. The item's ``text`` and
    ``text_hash`` were then both computed from that wrong span, so they agreed
    with each other and `verify` resolved happily. **A citation pointing at the
    wrong text, self-consistently.**

    ``origins[i]`` is the index in ``content`` that folded character ``i`` came
    from. Characters are grouped exactly when they compose -- detected by
    normalising a pair jointly and separately and seeing whether the results
    differ -- because a rule based on combining class alone misses the
    halfwidth voiced marks, whose combining class is 0.

    **Cached, and the origins are read-only so that the cache cannot hand two
    callers the same mutable map.** Confirmation folds a candidate's document
    once for the phrase rule and again for the coverage rule, and a query with
    fifty candidates folded 84 documents to look at 50 -- 25% of the time a
    `build_context` call took, spent re-deriving an answer it already had.
    """
    # A document larger than this is not cached. `origins` holds one entry per
    # folded character, so the cache costs memory in proportion to what it
    # holds, and 64 copies of a 10 MiB document is not a cache -- it is a leak
    # with a hit rate. Above the limit the work is simply done again.
    if len(content) > _CACHEABLE:
        return _fold(content)
    return _folded(content)


#: Characters. Comfortably above a real document (6,811 measured across two
#: sibling repositories) and far below anything that would hurt to hold 64 of.
#:
#: **That argument counted the document and not the map.** `origins` used to be
#: a tuple of Python integers, one per folded character, and a tuple of 262,144
#: of those is 9,216 KiB -- 36 times the text it describes. Sixty-four of them
#: is 608 MiB, in a library whose whole claim is that it runs beside an editor
#: and a model on somebody's laptop. Measured by `tools/measure_memory.py`.
_CACHEABLE: Final = 262_144


#: 64 rather than more: a query confirms at most `candidate_limit` documents and
#: folds each of them twice, so this is sized to hold one query's working set.
@lru_cache(maxsize=64)
def _folded(content: str) -> tuple[str, Origins]:
    return _fold(content)


def _fold(content: str) -> tuple[str, Origins]:
    # ASCII cannot compose and its casefold is one character for one character
    # (`A`-`Z` map to `a`-`z`, everything else is unchanged), so the offsets are
    # their own map and none of the work below is needed. This is every corpus
    # of English or of source code.
    if content.isascii():
        return content.lower(), None

    # Text that is **already NFKC** has nothing for the walk below to find:
    # normalising any part of it returns that part, so no two characters
    # compose and no character expands. All that is left is the casefold, and
    # `str.casefold` on the whole string is the same as folding each character
    # -- Unicode case folding has no context-dependent rules.
    #
    # `is_normalized` is a C quick-check rather than a normalise-and-compare,
    # so this costs one pass and no allocation. Measured on 6,811 characters,
    # the size a real document is:
    #
    #     japanese    4.34 ms -> 0.035 ms
    #     greek       4.17 ms -> 0.033 ms
    #     turkish     4.00 ms -> 0.033 ms
    #     german      3.92 ms -> 1.200 ms   (`ß` casefolds to `ss`)
    #
    # A query folds up to `candidate_limit` documents, so on ordinary Japanese
    # this is most of what a build spends outside the index. The two shapes it
    # does not help -- fullwidth ASCII (`２`, which NFKC rewrites) and one
    # character folding to several -- fall through unchanged.
    if unicodedata.is_normalized("NFKC", content):
        return _casefold_only(content)

    folded: list[str] = []
    # Built as an `array` rather than a list, so the *transient* cost of
    # folding a document that turns out to be even is 4 bytes a character
    # instead of 36.
    origins = array("I")
    #: Every piece so far has been one source character folding to one. See
    #: `Origins`: when that holds to the end there is nothing worth keeping.
    even = True
    index = 0
    while index < len(content):
        size = 1
        while index + size < len(content) and unicodedata.normalize(
            "NFKC", content[index : index + size + 1]
        ) != unicodedata.normalize("NFKC", content[index : index + size]) + unicodedata.normalize(
            "NFKC", content[index + size]
        ):
            size += 1
        piece = unicodedata.normalize("NFKC", content[index : index + size]).casefold()
        folded.append(piece)
        if size != 1 or len(piece) != 1:
            even = False
        origins.extend([index] * len(piece))
        index += size

    text = "".join(folded)
    if even:
        return text, None
    # Read-only, and that is the same guarantee the tuple used to give: the
    # cache hands this value to every caller that folds the same document, and
    # one of them editing it would move every later anchor into that document.
    return text, memoryview(origins).toreadonly()


def _casefold_only(content: str) -> tuple[str, Origins]:
    """Fold text that is already NFKC, where only case can change.

    Casefolding still moves offsets: `ß` becomes `ss`, `ﬁ` becomes `fi`. But it
    never *shortens* anything, so when the folded text is the same length as
    the original nothing expanded, and the map is the identity. That check is
    two C calls, and it is the answer for every script whose casefold is a
    no-op or one-for-one -- which is all of CJK, Greek, Cyrillic and Turkish.
    """
    folded = content.casefold()
    if len(folded) == len(content):
        return folded, None

    # Something expanded. Walk to find out where, one character at a time --
    # still without the composition detection, which has nothing to detect
    # here.
    parts: list[str] = []
    origins = array("I")
    for index, character in enumerate(content):
        piece = character.casefold()
        parts.append(piece)
        origins.extend([index] * len(piece))
    return "".join(parts), memoryview(origins).toreadonly()


def _source_span(origins: Origins, content: str, start: int, end: int) -> Span:
    """The span of ``content`` a folded match at ``[start, end)`` came from.

    **A source character the match ends inside is included**, which is the
    whole reason this is a function rather than two calls to `_origin`.

    One source character can fold to several: `ﬁ` to `fi`, `㍿` to `株式会社`.
    A match that ends part-way through one of those expansions maps its end to
    the origin of a character the match already covers, so mapping both ends
    alone cuts the source short:

    | text | question | folded match | mapped ends | quoted |
    |---|---|---|---|---|
    | ``0㍿`` | ``0株`` | ``[0, 2)`` | ``[0, 1)`` | ``0`` -- the wrong text |
    | ``㍿`` | ``株式`` | ``[0, 2)`` | ``[0, 0)`` | ``""`` -- nothing at all |

    The second row is the sharper failure only because it is louder. Both are
    an anchor pointing at text that does not contain the quotation it claims,
    and the empty one still resolves RESOLVED: the empty string really is at
    that offset.

    So the end is carried out to the end of the source character that produced
    the last folded character of the match. It contains the match; it merely
    contains more, which is what a citation into one character has to do.

    Found by a property test over text that folds unevenly -- the one shape no
    document in the corpus has in quantity, and the same blind spot that once
    let anchor offsets be computed in folded space and applied to the original.
    It had not reached a reader: every result is widened to its sentence before
    it becomes one, and no caller in this library passes a `context` of zero.
    "Nothing calls it that way today" is not a property of an anchor.
    """
    begins = _origin(origins, start, len(content))
    # `end - 1` is inside the map: `end > start >= 0` and `start` is a position
    # a `find` succeeded at, so the run has at least one folded character.
    last = (end - 1 if origins is None else origins[end - 1]) + 1
    return Span(begins, max(_origin(origins, end, len(content)), last))


def _origin(origins: Origins, at: int, length: int) -> int:
    """The index in the original string for folded index ``at``.

    Past the end maps to ``length``, so a span that ends on the last folded
    character ends at the end of the document rather than one short of it.
    """
    if origins is None:
        return min(at, length)
    return origins[at] if at < len(origins) else length


def _confirm_by_coverage(
    content: str, terms: Sequence[str], confirmation: Confirmation | None = None
) -> list[Span]:
    """Confirm when enough of the question's content is present, and say where.

    **A fallback, never a replacement.** It runs only where the phrase rule
    found nothing -- which today means the candidate is rejected outright -- so
    it can turn a rejection into a result and never the other way round. That
    is what keeps ADR-0007's guarantee intact: the index still over-generates
    and confirmation still decides.

    The *where* is half the job and used to be wrong. Taking each term's first
    occurrence pointed the item at whichever line mentioned the subject
    earliest -- reliably the heading, because a heading is a document's first
    mention of what it is about. The package then carried a title where the
    evidence should have been. Terms are located together instead: the answer
    is where they crowd, not where the first one happens to sit.
    """
    settings = confirmation or DEFAULT_CONFIRMATION
    total = sum(len(term) for term in terms)
    if not total:
        return []

    folded, origins = _fold_with_origins(content)
    located: list[tuple[str, list[int]]] = []
    matched = 0
    for term in terms:
        at, piece = _longest_present(term, folded)
        if at == -1:
            continue
        matched += _counted(term, piece, settings.inflection_tail)
        located.append((piece, _occurrences(piece, folded)))

    if matched / total < settings.coverage_threshold or not located:
        return []

    # Anchor on the longest term -- the most specific one -- and take each
    # other term's occurrence nearest to it. Ties resolve to the earlier
    # position, so the result does not depend on iteration order (ADR-0003).
    piece, positions = max(located, key=lambda pair: (len(pair[0]), -pair[1][0]))
    best_spans: list[Span] | None = None
    best_spread = -1
    for anchor in positions:
        spans = [
            _source_span(origins, content, at, at + len(text))
            for text, occurrences in located
            for at in [min(occurrences, key=lambda p: (abs(p - anchor), p))]
        ]
        spread = max(s.end for s in spans) - min(s.start for s in spans)
        if best_spans is None or spread < best_spread:
            best_spans, best_spread = spans, spread

    assert best_spans is not None  # `positions` is non-empty by construction.
    return sorted(best_spans, key=lambda span: span.start)


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


#: How much a longer confirmation is worth. Multiplied by the fraction of the
#: query that was matched, so a document confirming on the whole question
#: outranks one confirming on part of it. Measured, not chosen: see
#: ``docs/adr/0019-how-much-of-the-question-was-confirmed.md``.
MATCH_WEIGHT: Final = 0.1

#: A confirmation this much weaker than the best one found for the same query
#: is not evidence. Relative rather than absolute, because "how much of the
#: question a document contains" only means something next to what the rest of
#: the corpus managed: five words is a lot in one corpus and nothing in
#: another.
#:
#: Swept on train and confirmed held-out. Every value from 0.8 to 1.0 scores
#: identically -- train 88.9% / 96.5% / 3.6%, held-out 72.2% / 98.6% / 2.8% --
#: and **costs no recall at all** against the floor turned off, while taking
#: the trap rate from 21.4% to 3.6% on train and 30.6% to 2.8% held-out.
#:
#: Measured, not chosen, as of 2026-09-05: the sweep moves the trap rate
#: 16.7 points across this knob's range. A setting now. It is chosen
#: *permissive*, which is the opposite of ADR-0018's choice and for a reason
#: that fits both: at 1.0 a document is discarded unless its evidence is the
#: strongest found, and every case in this corpus has exactly one answer, so
#: the corpus cannot show the cost of that. A real notes folder holds two files
#: that both answer a question constantly. Picking 1.0 would be optimising for
#: a property of the fixtures.
RELATIVE_MATCH_FLOOR: Final = 0.8


def _confirm(content: str, needles: Sequence[str]) -> tuple[list[Span], int]:
    """Every occurrence of the longest needle present, and how long it was.

    The length is the second half of the answer and used not to be returned at
    all. Confirmation was a yes or no, so a document that matched five words of
    a six-word question ranked level with one that matched all six -- and where
    the sixth word is the *subject*, that is the difference between the right
    document and a near-miss about something else.
    """
    folded, origins = _fold_with_origins(content)
    found: list[Span] = []
    for needle in sorted(needles, key=len, reverse=True):
        start = folded.find(needle)
        while start != -1 and len(found) < 64:
            found.append(_source_span(origins, content, start, start + len(needle)))
            start = folded.find(needle, start + 1)
        if found:
            return found, len(needle)
    return [], 0


#: Characters that end a sentence outright. CJK punctuation is unambiguous:
#: nothing else uses these, so no lookahead is needed.
_HARD_STOPS: Final = "。．！？!?"

#: A full stop ends a sentence in Latin script only when whitespace or the
#: end of the text follows it. ``2.4kg`` is therefore not a sentence end, and
#: a rule that thought it were would return ``4kg.`` as the evidence for how
#: heavy a tent is.
#:
#: **``e.g.`` is not protected, and this comment used to claim it was.** A
#: space follows that full stop, so the window starts after it. Protecting it
#: needs a list of abbreviations, which is a per-language resource -- ADR-0007
#: refused a segmenter, ADR-0018 a stopword list and ADR-0019 a word list,
#: each for the same reason. So the cost is a window that starts one clause
#: late, and the claim is withdrawn rather than the list added.
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

    # A sentence does not begin with the space after the previous full stop.
    # `_sentence_start` returns the index just past the terminator, which is
    # that space, so every window opening on a soft stop carried one -- a
    # citation reading " The tent weighs 2.4kg." Bounded by the match, so a
    # window can never eat into what it was widened around.
    while start < span.start and content[start].isspace():
        start += 1

    line_end = content.find("\n", span.end, ceiling)
    end = min(ceiling if line_end == -1 else line_end, _sentence_end(content, span.end, ceiling))

    return Span(start, max(end, span.end))


def _section_name(document: Document, offset: int) -> str:
    section: Section | None = document.section_at(offset)
    return section.heading if section is not None else ""


@dataclass(frozen=True, slots=True)
class Confirmation:
    """The three numbers that decide whether a candidate becomes a result.

    They were module constants until `tools/measure_sensitivity.py` moved each
    one and re-scored the whole labelled corpus. All three are on **cliffs** --
    the sweep's word for a value that swings the numbers when nudged, which is
    the signature of a number fitted to one corpus:

        INFLECTION_TAIL       16.7 recall points between 1 and 2
        RELATIVE_MATCH_FLOOR  16.7 trap points between 0.5 and 0.8
        COVERAGE_THRESHOLD    13.3 trap points between 0.6 and 1.0

    A plateau would have meant the value was not doing fine work and somebody
    else's corpus would not need a different one. A cliff means the opposite,
    and leaving it as a constant meant the only way to disagree was to edit the
    source.

    **The defaults do not move.** Every number in `docs/measurements.md` was
    taken on these, and each is on the safe shoulder of its cliff rather than
    the steep side -- 2 and 3 score identically for the tail, 0.8 and 0.95 for
    the floor. What changes is that disagreeing is now possible.
    """

    #: How much of a question must be present before coverage confirms it.
    #: Lowering it to 0.6 buys 0.6 recall points and costs 13.3 trap points on
    #: this corpus, which is the measurement that made 1.0 a decision rather
    #: than a guess -- ADR-0018 recorded that the corpus could not separate 0.8
    #: from 1.0, and it now can.
    coverage_threshold: float = COVERAGE_THRESHOLD
    #: How weak a match may be, against the best found for the same query.
    relative_match_floor: float = RELATIVE_MATCH_FLOOR
    #: Characters allowed to hang off the end of a term before a stem match
    #: stops counting as the whole word. **The most corpus-shaped of the
    #: three**: it exists because Japanese glues grammar to its nouns, and 2 is
    #: the number that suits Japanese. A language with longer suffixes needs
    #: more, and at 1 this corpus loses 16.7 recall points.
    inflection_tail: int = INFLECTION_TAIL

    def __post_init__(self) -> None:
        if not 0.0 < self.coverage_threshold <= 1.0:
            raise ValueError(
                f"coverage_threshold is a share of a question and must be above 0 "
                f"and at most 1, not {self.coverage_threshold}"
            )
        if not 0.0 <= self.relative_match_floor <= 1.0:
            raise ValueError(
                f"relative_match_floor is a share of the best match and must be "
                f"between 0 and 1, not {self.relative_match_floor}"
            )
        if self.inflection_tail < 0:
            raise ValueError(
                f"inflection_tail counts characters and cannot be negative, "
                f"not {self.inflection_tail}"
            )


#: What every number in `docs/measurements.md` was measured on.
DEFAULT_CONFIRMATION: Final = Confirmation()
