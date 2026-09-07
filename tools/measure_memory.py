"""What a query holds in memory while it runs, and where it goes.

    python tools/measure_memory.py

Every number in `docs/measurements.md` is time or accuracy. Nothing had ever
measured **space**, and the library has one deliberate cache in it — `_folded`,
64 entries, documents up to 256 KiB — sized by an argument about character
counts:

    64 copies of a 10 MiB document is not a cache -- it is a leak with a
    hit rate.

That argument counts the *document*. The cached value is a pair, and the second
half of it is the map from folded index back to source index. As a tuple of
Python integers that was **one 36-byte object per folded character**: 9,216 KiB
for a 256 KiB document, 608 MiB at the cache's full 64.

The local-first claim is what makes this worth a number rather than a shrug.
This runs beside an editor and a model on somebody's laptop; a retrieval
library that quietly takes half a gigabyte to answer a question has broken the
one promise that distinguishes it from a hosted index.

Four shapes, because which one a document has decides everything:

    ascii                every corpus of English or of source code
    japanese             kana and kanji, no fullwidth
    japanese, fullwidth  ＵＲＬ and １２３, ordinary in Japanese writing
    uneven (㍿)          one character folding to four

Only the last needs a map at all. The first three fold one character for one,
which is the identity — and that is not a rare case optimised for its own sake:
it is **780 of the 780 documents in the evaluation corpus**.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tsumugi.application.search import _CACHEABLE, _fold, _folded  # noqa: E402

KIB = 1024

#: What one entry of the map cost when it was a tuple of Python integers: eight
#: bytes of pointer in the tuple plus a twenty-eight byte `int` object. Written
#: as a constant because the point of this tool is the comparison, and the old
#: representation is not around to be measured any more.
BYTES_PER_TUPLE_ENTRY = 36

SHAPES = (
    ("ascii", "the tent weighs 2.4kg. "),
    ("japanese", "テントの重量は2.4kg。"),
    ("japanese, fullwidth", "重量は２．４ｋｇ、ＵＲＬは後述。"),
    ("uneven (㍿)", "㍿山田の記録は㍿による。"),
)


def _row(label: str, *cells: object) -> None:
    print(f"| {label:<22} | " + " | ".join(f"{cell:>16}" for cell in cells) + " |")


def _kib(value: float) -> str:
    return f"{value / KIB:,.0f} KiB"


def measure_fold() -> dict[str, dict[str, Any]]:
    """One document at the cache's own size limit, in each shape."""
    findings: dict[str, dict[str, Any]] = {}
    for name, unit in SHAPES:
        content = (unit * (_CACHEABLE // len(unit) + 1))[:_CACHEABLE]
        folded, origins = _fold(content)
        findings[name] = {
            "content_bytes": sys.getsizeof(content),
            "folded_bytes": sys.getsizeof(folded),
            "identity": origins is None,
            "map_bytes": 0 if origins is None else origins.nbytes + sys.getsizeof(origins),
            "map_as_a_tuple": len(folded) * BYTES_PER_TUPLE_ENTRY,
        }
    return findings


def main() -> int:
    print("## What one folded document costs\n")
    print("`_CACHEABLE` is the size limit on what may be cached, in *characters*.\n")
    _row("", "content", "map, as a tuple", "map, now", "map/content")
    print("|" + "-" * 24 + "|" + ("-" * 18 + "|") * 4)

    findings = measure_fold()
    for name, found in findings.items():
        _row(
            name,
            _kib(found["content_bytes"]),
            _kib(found["map_as_a_tuple"]),
            "identity" if found["identity"] else _kib(found["map_bytes"]),
            f"{found['map_bytes'] / found['content_bytes']:.2f}x",
        )

    was = max(f["map_as_a_tuple"] + f["folded_bytes"] for f in findings.values())
    now = max(f["map_bytes"] + f["folded_bytes"] for f in findings.values())
    print()
    print(f"One cached entry, worst shape: **{_kib(was)}** -> **{_kib(now)}**")
    print(
        f"The cache holds 64: **{was * 64 / KIB / KIB:,.0f} MiB** -> "
        f"**{now * 64 / KIB / KIB:,.0f} MiB**"
    )
    print()
    print("Two changes, and the first is the one that matters on real text.")
    print()
    print("A fold that produced one character for one character has no map worth keeping.")
    print("When a map is needed it holds four bytes an entry rather than a Python integer.")

    _folded.cache_clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
