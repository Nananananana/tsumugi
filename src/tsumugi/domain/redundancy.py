"""Finding near-duplicates, and saying so rather than deleting them.

A personal corpus is full of them: the same decision written in a note,
restated in a summary, quoted in a later document, pasted into a message. Sent
whole to a model they waste budget, and -- worse than the waste -- one idea
starts to look like four independent sources, because repetition reads as
corroboration.

**This module marks. It never removes** ([ADR 0008](../../docs/adr/0008-redundancy-is-proposed.md)).
The asymmetry is the reason: dropping a genuinely redundant passage saves a few
hundred tokens, and dropping one that *looked* redundant -- the version with the
exception, the later note that reversed the decision, the one with the number --
silently removes the answer. Near-duplicates differ in exactly the part that
matters, because they are near-duplicates *because* the difference is a
correction.

Detection is character shingles and set containment. No model, no embedding, no
learned threshold: same texts, same verdict, every time (ADR-0003).

**What it sees, and what it does not.** It finds a passage that was *copied* --
verbatim, reflowed, embedded in something longer, or lightly edited. It does
not find a passage that says the same thing in different words, and it does not
find a passage that supersedes another by correcting a value. The second of
those was measured and is not a tuning problem: "the tent weighs 2.4kg" and
"the tent weighs 3.1kg" share 0.417 containment, while "the tarp weighs 3.1kg"
shares 0.167 -- the correction and the unrelated statement are not separable by
character overlap, because the difference between them is meaning. Recognising
a superseded version needs semantics that the deterministic core deliberately
does not have (ADR-0015).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = ["DEFAULT_THRESHOLD", "SHINGLE", "Similarity", "shingles", "similarity"]

#: Characters per shingle. Five works across scripts without knowing about
#: words: long enough that two unrelated Japanese sentences rarely share one,
#: short enough that an edit inside a sentence does not destroy every shingle
#: around it.
SHINGLE: Final = 5

#: Above this, two passages are reported as near-duplicates.
#:
#: Not guessed. Measured containment over real passages:
#:
#:     verbatim copy                     1.000
#:     copy inside a longer document     1.000
#:     copy, one clause changed          0.889
#:     copy, reflowed to a new width     0.873
#:     ---------------------------------------  the gap
#:     same subject, corrected value     0.417
#:     different subject, same shape     0.167
#:     same topic, rewritten             0.000
#:     unrelated                         0.000
#:
#: 0.75 sits in the gap, with 0.456 of margin on either side. The numbers and
#: what they do not say are in docs/measurements.md.
DEFAULT_THRESHOLD: Final = 0.75


@dataclass(frozen=True, slots=True)
class Similarity:
    """How much two passages have in common, and in which direction."""

    #: Shared shingles over the smaller passage's shingles. Catches "one of
    #: these is a copy of part of the other", which Jaccard misses when the
    #: lengths differ.
    containment: float
    #: Shared over combined. Catches "these are two copies of one thing".
    jaccard: float

    @property
    def score(self) -> float:
        """One number, and it is the generous one.

        Containment, because the case that matters is a short passage that is
        wholly inside a longer one -- a quotation, an excerpt, a paragraph
        pasted into a summary. Being generous is safe here precisely because
        the finding is a *mark* and never a deletion.
        """
        return self.containment

    def is_near_duplicate(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.score >= threshold

    def describe(self) -> str:
        return f"{self.score:.0%} overlap"


def shingles(text: str, size: int = SHINGLE) -> frozenset[str]:
    """Overlapping character runs of ``text``, normalized.

    NFKC and case-folded, and whitespace collapsed, so that a passage reflowed
    to a different line width is still recognisable as the same passage. That
    is the commonest way a duplicate stops looking like one.
    """
    folded = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    if len(folded) < size:
        return frozenset({folded}) if folded else frozenset()
    return frozenset(folded[i : i + size] for i in range(len(folded) - size + 1))


def similarity(left: str, right: str, size: int = SHINGLE) -> Similarity:
    """How alike two passages are. Symmetric, deterministic, model-free."""
    a, b = shingles(left, size), shingles(right, size)
    if not a or not b:
        return Similarity(containment=0.0, jaccard=0.0)

    shared = len(a & b)
    return Similarity(
        containment=shared / min(len(a), len(b)),
        jaccard=shared / len(a | b),
    )


def mark_duplicates(
    texts: Sequence[str], *, threshold: float = DEFAULT_THRESHOLD
) -> dict[int, tuple[int, Similarity]]:
    """Which passages duplicate an earlier one, and which one.

    ``texts`` must already be in the order the caller prefers -- best first.
    Each passage is compared against every *earlier* one, so the first member
    of a cluster is the one that survives and the rest point back at it.

    **The ordering decides which survives, and this module does not choose
    it.** Redundancy says two passages are alike; it has no way to know which
    is right, and guessing would systematically prefer whichever heuristic was
    picked -- see ADR-0015.
    """
    marks: dict[int, tuple[int, Similarity]] = {}
    for index in range(1, len(texts)):
        best: tuple[int, Similarity] | None = None
        for earlier in range(index):
            if earlier in marks:
                # Compare against cluster heads only, so a chain of near-copies
                # collapses to one survivor rather than a chain of pointers.
                continue
            found = similarity(texts[earlier], texts[index])
            if found.is_near_duplicate(threshold) and (best is None or found.score > best[1].score):
                best = (earlier, found)
        if best is not None:
            marks[index] = best
    return marks
