"""Promises the documents make that a reader can check by following them.

Prompted by `musubi`, which found that `docs/contracts.md` told consumers to
load the contract from a path that did not exist in a development checkout --
true for anyone who had installed the wheel, false for everyone working on it,
and **nothing in the repository ever ran the sentence.**

tsumugi's equivalent instruction leads with `tsumugi.contract_schema()`, which
the conformance suite calls, so the API half was already exercised. The file
paths beside it were not, and two of them pointed at nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: `[text](target)`, skipping URLs, mail and same-page anchors. The fragment is
#: dropped: `file.md#section` is a claim about the file, and checking headings
#: as well would make this fail on a renamed section, which is a different and
#: much noisier promise.
LINK = re.compile(r"\[[^\]]*\]\((?!https?:|mailto:|#)([^)#]+)(?:#[^)]*)?\)")


def _documents() -> list[Path]:
    found = [
        path
        for path in ROOT.rglob("*.md")
        if not {".git", ".venv", "node_modules"} & set(path.parts)
    ]
    # `for x in []: assert` is green. A renamed directory or a typo in the
    # glob would turn every check below into a check of nothing, and nothing
    # would go red -- `akashi` confirmed the shape by pointing its `SRC` at a
    # directory that does not exist and watching 35 tests pass.
    assert found, f"no markdown under {ROOT}; this is measuring nothing"
    return found


def test_every_local_link_in_every_document_resolves() -> None:
    """A link to a file in this repository points at a file in this repository.

    The failure this catches is not a typo. It is a file that **moved** while
    the sentence describing it stayed, which is how both broken links here were
    born: the schema went from `schemas/` to `src/tsumugi/schemas/` so that it
    would ship inside the wheel, the link text was updated to say so, and the
    href was not. The two disagreed inside the same line -- text naming the new
    path, target still pointing at the old one -- and every rendering of the
    document showed a confident link to nothing.

    Documents are not exercised by anything else here. The demo runs, the
    examples run, the fixtures are regenerated and compared; prose is read by
    people, at a rate of about once. This is the cheapest thing that reads it
    every time.
    """
    broken = [
        f"{path.relative_to(ROOT).as_posix()} -> {target}"
        for path in _documents()
        for target in LINK.findall(path.read_text(encoding="utf-8"))
        if not (path.parent / target.strip()).exists()
    ]
    assert not broken, "links to files that are not there:\n  " + "\n  ".join(broken)


def test_the_documented_way_to_read_the_contract_is_the_one_that_is_tested() -> None:
    """The instruction consumers are given is the one the suite exercises.

    `docs/context-package.md` offers two routes to the schema: call
    `tsumugi.contract_schema()`, or vendor the file. The first is named first
    on purpose -- it is the one `test_contract_conformance.py` runs, so it
    cannot rot without a test going red, and it is the reason the accessor
    exists at all rather than a documented path into the package.

    This asserts the ordering survives an edit. A version of that page leading
    with the file path would be handing consumers the half of the instruction
    that nothing executes.
    """
    page = (ROOT / "docs" / "context-package.md").read_text(encoding="utf-8")
    accessor = page.index("contract_schema()")
    vendoring = page.index("Vendoring the file directly")
    assert accessor < vendoring, (
        "docs/context-package.md describes vendoring the schema file before it "
        "describes the accessor. The accessor is the route the conformance "
        "suite exercises; the file path is not."
    )


#: `## Some Heading` -> `#some-heading`, the way GitHub builds an anchor.
HEADING = re.compile(r"(?m)^#{1,6} (.+?)\s*$")
IN_PAGE = re.compile(r"\]\(#([^)]+)\)")


def _anchor(heading: str) -> str:
    """GitHub's slug: lowercase, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^a-z0-9 _-]", "", heading.lower()).strip().replace(" ", "-")


def test_every_in_page_link_points_at_a_heading_that_exists() -> None:
    """A table of contents that points at nothing looks exactly like one that works.

    The links resolving test above skips fragments on purpose -- checking
    headings as well as files would make it fail on a renamed section, which is
    a noisier promise. This is the narrow version: a link with **only** a
    fragment is a claim about this document alone, and nothing outside it can
    change the answer.

    Added when the README grew a "Where to start" table, which is the first
    thing a reader meets and the first thing to rot.
    """
    broken: list[str] = []
    for path in _documents():
        text = path.read_text(encoding="utf-8")
        anchors = {_anchor(h) for h in HEADING.findall(text)}
        broken += [
            f"{path.relative_to(ROOT).as_posix()} -> #{target}"
            for target in IN_PAGE.findall(text)
            if target not in anchors
        ]
    assert not broken, "links to headings that are not there:\n  " + "\n  ".join(broken)
