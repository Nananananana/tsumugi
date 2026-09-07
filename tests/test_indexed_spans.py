"""What counts as one searchable unit, and why the pieces must tile.

`_own_spans` decides what the index scores and therefore what a package can
return. `Document.sections` **nests** -- a level-1 heading spans everything
under it, including its level-2 children -- so a section contributes only the
text between its own start and its first child. The pieces tile: every
character is indexed exactly once.

That last sentence is load-bearing and was untested. Indexing a paragraph once
per ancestor does not merely inflate the index; `ContextPackage` refuses to be
built, because the same span arrives as both an item and an omission.

Written after `python tools/mutate.py` survived six mutations here, three of
them inside the `children` computation. Every fixture in the suite used **flat**
sections, so no test could tell "the text under this heading" from "this
heading and everything beneath it".
"""

from __future__ import annotations

import sqlite3
from itertools import pairwise

import pytest

from tests.helpers import build_document
from tsumugi.application.build_context import build_context
from tsumugi.domain.budget import Budget
from tsumugi.domain.document import Block, Document, Section
from tsumugi.domain.span import Span
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.index.fts import FtsIndex, _own_spans
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

NL = chr(10)

#: A level-1 heading with two level-2 children under it. The shape every
#: markdown document with sub-headings has, and the one no fixture had.
NESTED = (
    "# Gear"
    + NL * 2
    + "General notes."
    + NL * 2
    + "## Tent"
    + NL * 2
    + "It weighs 2.4kg."
    + NL * 2
    + "## Fuel"
    + NL * 2
    + "250g of gas."
    + NL
)


def _nested_document() -> Document:
    """`NESTED`, with the sections a markdown parser produces for it."""
    tent = NESTED.index("## Tent")
    fuel = NESTED.index("## Fuel")
    return build_document(
        "gear.md",
        NESTED,
        sections=(
            # The parent spans everything, including both children.
            Section(heading="Gear", level=1, span=Span(0, len(NESTED))),
            Section(heading="Tent", level=2, span=Span(tent, fuel)),
            Section(heading="Fuel", level=2, span=Span(fuel, len(NESTED))),
        ),
    )


class TestThePiecesTile:
    def test_a_parent_stops_where_its_first_child_begins(self) -> None:
        """ "The part under this heading", not "this heading and all beneath it"."""
        spans = _own_spans(_nested_document())
        assert spans[0] == Span(0, NESTED.index("## Tent"))

    def test_every_character_is_covered_exactly_once(self) -> None:
        """The tiling property, stated as arithmetic.

        Overlap is the failure that matters: the same paragraph indexed under
        two ancestors arrives as both an item and an omission, and the package
        refuses to build.
        """
        spans = sorted(_own_spans(_nested_document()), key=lambda s: s.start)
        assert spans[0].start == 0
        assert spans[-1].end == len(NESTED)
        for earlier, later in pairwise(spans):
            assert earlier.end == later.start, f"{earlier} and {later} do not meet"

    def test_each_piece_holds_its_own_text_and_no_child_s(self) -> None:
        document = _nested_document()
        held = [span.slice(document.content) for span in _own_spans(document)]
        parent = next(text for text in held if "General notes." in text)
        assert "It weighs 2.4kg." not in parent, parent
        assert "250g of gas." not in parent, parent
        assert any("It weighs 2.4kg." in text for text in held)
        assert any("250g of gas." in text for text in held)

    def test_three_sections_produce_three_pieces(self) -> None:
        """A parent that vanished into its children would give two."""
        assert len(_own_spans(_nested_document())) == 3


class TestAnEmptyPieceIsNotIndexed:
    def test_a_section_that_is_entirely_front_matter_is_dropped(self) -> None:
        """`end > start`, not `>=`.

        A zero-length span is a row matching nothing, with an anchor that
        resolves to the empty string -- a citation to nowhere.
        """
        content = "---" + NL + "source_url: https://example.com/a" + NL + "---" + NL + "Body." + NL
        fence = content.index("---", 3) + len("---")
        document = build_document(
            "a.md",
            content,
            sections=(
                Section(heading="", level=0, span=Span(0, fence)),
                Section(heading="Body", level=1, span=Span(fence, len(content))),
            ),
            blocks=(Block(kind="front_matter", span=Span(0, fence)),),
        )
        spans = _own_spans(document)
        assert all(span.end > span.start for span in spans), spans
        assert all(span.start >= fence for span in spans), spans

    def test_a_zero_length_section_is_dropped(self) -> None:
        content = "Body." + NL
        document = build_document(
            "b.md",
            content,
            sections=(
                Section(heading="empty", level=1, span=Span(0, 0)),
                Section(heading="Body", level=1, span=Span(0, len(content))),
            ),
        )
        assert _own_spans(document) == [Span(0, len(content))]


class TestTheDefaultCandidateLimit:
    def test_a_search_with_no_limit_returns_at_most_fifty(
        self, connection: sqlite3.Connection
    ) -> None:
        """The cap is documented as reaching `omissions` under
        `truncated_by_cap`, and its default value was untested: a caller that
        passes no limit must get the documented one."""
        index = FtsIndex(connection)
        for number in range(60):
            index.add(build_document(f"n{number:03d}.md", f"# N{number}{NL * 2}alpha beta{NL}"))
        assert len(index.search("alpha")) == 50

    def test_an_explicit_limit_is_honoured(self, connection: sqlite3.Connection) -> None:
        index = FtsIndex(connection)
        for number in range(60):
            index.add(build_document(f"n{number:03d}.md", f"# N{number}{NL * 2}alpha beta{NL}"))
        assert len(index.search("alpha", limit=7)) == 7


@pytest.mark.parametrize("heading_count", [1, 2, 5])
def test_a_flat_document_still_tiles(heading_count: int) -> None:
    """The case that already worked, kept so the nested fix cannot break it."""
    parts = [f"# H{number}{NL * 2}text {number}{NL * 2}" for number in range(heading_count)]
    content = "".join(parts)
    starts = []
    offset = 0
    for part in parts:
        starts.append(offset)
        offset += len(part)
    sections = tuple(
        Section(heading=f"H{number}", level=1, span=Span(start, start + len(parts[number])))
        for number, start in enumerate(starts)
    )
    spans = _own_spans(build_document("flat.md", content, sections=sections))
    assert len(spans) == heading_count
    assert spans[0].start == 0
    assert spans[-1].end == len(content)


class TestTwoSectionsWithTheSameSpan:
    """The `!= section.span` guard, and the only shape that needs it.

    `children` counts a later section as a child when the current one
    *contains* it **and** the two are not the same span. Dropping the second
    clause makes a section its own child: `end` becomes its own start, `end >
    start` is false, and the piece is silently not indexed at all.

    Reachable whenever a document has a heading whose section covers exactly
    what an outer one covers -- a lone `#` heading at the top of a file whose
    only content is a `##` heading, for instance -- and by any parser that
    emits a duplicate.
    """

    def test_a_duplicate_span_does_not_delete_the_piece(self) -> None:
        content = "# Gear" + NL * 2 + "It weighs 2.4kg." + NL
        document = build_document(
            "dup.md",
            content,
            sections=(
                Section(heading="Gear", level=1, span=Span(0, len(content))),
                Section(heading="Gear again", level=2, span=Span(0, len(content))),
            ),
        )
        spans = _own_spans(document)
        assert spans == [Span(0, len(content))], (
            "the twin sections each contributed the same text, so the index holds "
            "it twice and a package built from it refuses to assemble"
        )

    def test_the_text_is_still_reachable_through_the_index(
        self, connection: sqlite3.Connection
    ) -> None:
        """End to end, because a dropped span looks exactly like a bad query."""
        content = "# Gear" + NL * 2 + "It weighs 2.4kg." + NL
        index = FtsIndex(connection)
        index.add(
            build_document(
                "dup.md",
                content,
                sections=(
                    Section(heading="Gear", level=1, span=Span(0, len(content))),
                    Section(heading="Gear again", level=2, span=Span(0, len(content))),
                ),
            )
        )
        assert index.count() >= 1
        assert index.search("weighs"), "the passage is not findable"

    def test_a_package_can_be_built_from_a_document_with_twin_sections(
        self, connection: sqlite3.Connection
    ) -> None:
        """The consequence, end to end.

        With the same span indexed twice, one copy becomes an item and the
        other an omission, and `ContextPackage.__post_init__` refuses -- a
        build that raises rather than a package that is wrong, which is the
        right failure and still a failure.
        """
        content = "# Gear" + NL * 2 + "It weighs 2.4kg." + NL
        twin = build_document(
            "dup.md",
            content,
            sections=(
                Section(heading="Gear", level=1, span=Span(0, len(content))),
                Section(heading="Gear again", level=2, span=Span(0, len(content))),
            ),
        )
        store = SqliteDocumentStore(connection)
        store.put(twin, corpus_root="/corpus")
        index = FtsIndex(connection)
        index.add(twin)

        package = build_context(
            "how heavy is it",
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=Budget.parse("characters:400"),
        )
        anchors = [(i.anchor.span.start, i.anchor.span.end) for i in package.items]
        omitted = [(o.span.start, o.span.end) for o in package.omissions]
        assert len(anchors) == len(set(anchors)), anchors
        assert not (set(anchors) & set(omitted)), "a span is both an item and an omission"


class TestOverlappingSectionsStillTile:
    """The shape that broke the promise, and the reason the rule got simpler.

    `_own_spans` said "every character is indexed exactly once". It held for
    nested sections and not for **overlapping** ones: `A(0,20)` beside
    `B(10,30)` indexed ten characters twice, because the old rule asked *is
    this section my child* rather than *where does the next one start*.

    A parser this project ships cannot produce that -- headings do not overlap
    -- but `register_parser` is public, and a stated guarantee that holds only
    for the shapes we happen to emit is not a guarantee.
    """

    def _overlapping(self) -> Document:
        content = "x" * 30
        return build_document(
            "overlap.md",
            content,
            sections=(
                Section(heading="A", level=1, span=Span(0, 20)),
                Section(heading="B", level=2, span=Span(10, 30)),
            ),
        )

    def test_no_character_is_indexed_twice(self) -> None:
        covered = [0] * 30
        for span in _own_spans(self._overlapping()):
            for position in range(span.start, span.end):
                covered[position] += 1
        assert max(covered) == 1, covered

    def test_no_character_is_missed(self) -> None:
        """Half of tiling. A rule that truncated too eagerly would pass the
        test above and quietly stop indexing the end of every document."""
        covered = [0] * 30
        for span in _own_spans(self._overlapping()):
            for position in range(span.start, span.end):
                covered[position] += 1
        assert min(covered) == 1, covered

    def test_a_section_is_never_stretched_past_its_own_end(self) -> None:
        """A later section that begins beyond this one does not extend it."""
        content = "y" * 40
        document = build_document(
            "gap.md",
            content,
            sections=(
                Section(heading="A", level=1, span=Span(0, 10)),
                Section(heading="B", level=1, span=Span(20, 40)),
            ),
        )
        spans = _own_spans(document)
        assert Span(0, 10) in spans, spans
        assert all(span.end <= 40 for span in spans)
