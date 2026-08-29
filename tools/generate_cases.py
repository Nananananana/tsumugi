"""Generate evaluation cases: a corpus with facts planted where we know.

    python tools/generate_cases.py --out tests/cases --per-genre 3

The genres are decoration. **The traps are the dataset** -- a generated corpus
of relevant documents measures nothing, because every retriever passes it. What
this plants, per case, is one answerable fact and one or more adversaries drawn
from ADR-0013's list: a lexically similar document about something else, a
near-duplicate, a superseded version whose differing sentence is the
correction, and required facts pushed below a budget line.

Deterministic. No model runs here, and no random seed either: the same
arguments produce byte-identical cases, so a regression in the fixtures is a
diff rather than a mystery.

**Every case is verified by an oracle before it is written** (ADR-0013). The
oracle reads only the labels and checks the case is solvable, non-trivial and
free of anything that looks like real personal data. A generator that plants a
trap wrongly produces a case that fails a *correct* implementation, and that
failure is expensive precisely because the instinct is to go looking in the
code.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsumugi.evaluation.dataset import load_case


@dataclass(frozen=True)
class Genre:
    """A subject, and the vocabulary a case in it is written from."""

    key: str
    language: str
    #: The thing the question asks about, and the thing the answer states.
    subject: str
    attribute: str
    answer: str
    #: A near-miss: same words, different subject.
    neighbour: str
    #: What the superseded version said before it was corrected.
    superseded_answer: str
    heading: str
    question: str


GENRES: tuple[Genre, ...] = (
    Genre(
        "mountaineering",
        "ja",
        "テント",
        "重量",
        "2.4kg、二人用",
        "キャンプ用タープ",
        "3.1kg、二人用",
        "装備",
        "テントの重量は",
    ),
    Genre(
        "employment-rules",
        "ja",
        "有給休暇",
        "付与日数",
        "初年度は10日、以降1日ずつ加算",
        "特別休暇",
        "初年度は8日、以降1日ずつ加算",
        "休暇",
        "有給休暇の付与日数は",
    ),
    Genre(
        "research-notes",
        "ja",
        "サンプル",
        "保存温度",
        "マイナス80度で凍結保存",
        "試薬",
        "マイナス20度で凍結保存",
        "保存条件",
        "サンプルの保存温度は",
    ),
    Genre(
        "recipes",
        "ja",
        "生地",
        "寝かせ時間",
        "冷蔵庫で12時間",
        "ソース",
        "冷蔵庫で3時間",
        "手順",
        "生地の寝かせ時間は",
    ),
    Genre(
        "meeting-minutes",
        "ja",
        "次回の会議",
        "開催日",
        "来月の第二火曜日",
        "報告会",
        "来月の第一金曜日",
        "決定事項",
        "次回の会議の開催日は",
    ),
    Genre(
        "gardening",
        "ja",
        "球根",
        "植え付け時期",
        "10月下旬から11月上旬",
        "種まき",
        "3月下旬から4月上旬",
        "作業予定",
        "球根の植え付け時期は",
    ),
    Genre(
        "infrastructure",
        "en",
        "the staging cluster",
        "node count",
        "six nodes across two zones",
        "the build farm",
        "four nodes in one zone",
        "Capacity",
        "how many nodes does the staging cluster have",
    ),
    Genre(
        "legal-memo",
        "en",
        "the notice period",
        "length",
        "sixty days in writing",
        "the cure period",
        "thirty days in writing",
        "Termination",
        "how long is the notice period",
    ),
    Genre(
        "travel",
        "en",
        "the ferry",
        "first departure",
        "06:40 from the north quay",
        "the shuttle bus",
        "07:15 from the north quay",
        "Schedule",
        "when is the first ferry departure",
    ),
    Genre(
        "code-review",
        "en",
        "the retry policy",
        "backoff",
        "exponential, capped at thirty seconds",
        "the rate limiter",
        "linear, capped at ten seconds",
        "Behaviour",
        "what backoff does the retry policy use",
    ),
)

#: Padding that is plausible prose and shares no vocabulary with any answer, so
#: it cannot accidentally become the thing a query matches.
_FILLER = {
    "ja": (
        "この項目は前回の棚卸しで見直した。\n"
        "担当は持ち回りで、引き継ぎ時に一度確認する。\n"
        "細かい経緯は別の記録に残してある。\n"
    ),
    "en": (
        "This entry was reviewed at the last stocktake.\n"
        "Ownership rotates, and is checked once at handover.\n"
        "The longer history is kept in a separate record.\n"
    ),
}


def _answer_document(genre: Genre, fact_id: str) -> str:
    return (
        f"---\ntitle: {genre.heading}\n---\n\n"
        f"# {genre.heading}\n\n"
        f"{{{{F:{fact_id}}}}}{genre.subject}の{genre.attribute}は{genre.answer}"
        f"{{{{/F}}}}。\n\n"
        f"{_FILLER[genre.language]}"
        if genre.language == "ja"
        else (
            f"---\ntitle: {genre.heading}\n---\n\n"
            f"# {genre.heading}\n\n"
            f"{{{{F:{fact_id}}}}}The {genre.attribute} of {genre.subject} is "
            f"{genre.answer}{{{{/F}}}}.\n\n"
            f"{_FILLER[genre.language]}"
        )
    )


def _superseded_document(genre: Genre, fact_id: str) -> str:
    """94% identical to the answer. The differing sentence is the correction.

    The trap most worth getting right and the easiest to get wrong: two
    passages that are nearly identical are usually nearly identical *because*
    the difference is a correction.
    """
    if genre.language == "ja":
        body = (
            f"{{{{F:{fact_id}}}}}{genre.subject}の{genre.attribute}は"
            f"{genre.superseded_answer}{{{{/F}}}}。\n\n"
            "※この記録は古い。改訂版を参照すること。\n"
        )
    else:
        body = (
            f"{{{{F:{fact_id}}}}}The {genre.attribute} of {genre.subject} is "
            f"{genre.superseded_answer}{{{{/F}}}}.\n\n"
            "NOTE: this record is out of date; see the revision.\n"
        )
    return f"# {genre.heading}\n\n{body}\n{_FILLER[genre.language]}"


def _near_miss_document(genre: Genre, fact_id: str) -> str:
    """Same vocabulary, different subject. The confirmation stage's exam."""
    if genre.language == "ja":
        body = (
            f"{{{{F:{fact_id}}}}}{genre.neighbour}の{genre.attribute}は"
            f"{genre.superseded_answer}{{{{/F}}}}。\n"
        )
    else:
        body = (
            f"{{{{F:{fact_id}}}}}The {genre.attribute} of {genre.neighbour} is "
            f"{genre.superseded_answer}{{{{/F}}}}.\n"
        )
    return f"# {genre.heading}\n\n{body}\n{_FILLER[genre.language]}"


def _bulk_document(genre: Genre, n: int) -> str:
    """Competition, so that a budget can actually bind."""
    if genre.language == "ja":
        return (
            f"# {genre.heading} 補足 {n}\n\n"
            f"{genre.subject}の運用について、過去の経緯を並べておく。\n"
            f"{_FILLER[genre.language] * 3}"
        )
    return (
        f"# {genre.heading}, appendix {n}\n\n"
        f"Background on how {genre.subject} has been handled before.\n"
        f"{_FILLER[genre.language] * 3}"
    )


def build_case(genre: Genre, variant: int) -> tuple[str, dict[str, str], dict[str, object]]:
    """One case: its id, its marked-up documents, and its manifest."""
    case_id = f"{genre.language}-{genre.key}-{variant:02d}"
    answer, superseded, near_miss = "answer", "superseded", "near-miss"

    documents = {
        "current.md": _answer_document(genre, answer),
        "older.md": _superseded_document(genre, superseded),
        "neighbour.md": _near_miss_document(genre, near_miss),
    }

    traps: dict[str, object] = {
        # Superseded and near-duplicate content is *marked*, never silently
        # deleted (ADR-0008), so the rule to expect is redundant_candidate.
        superseded: {"kind": "superseded", "expect_omission_rule": "redundant_candidate"},
        near_miss: {"kind": "lexical_near_miss"},
    }

    # Variant 1 squeezes the budget: the required fact has to survive
    # competition, and everything dropped has to say budget_exhausted.
    if variant == 1:
        for n in range(4):
            documents[f"appendix-{n}.md"] = _bulk_document(genre, n)
        budget = {"unit": "characters", "limit": 300}
    else:
        budget = {"unit": "characters", "limit": 1200}

    manifest: dict[str, object] = {
        "case_id": case_id,
        "genre": genre.key,
        "language": genre.language,
        "question": genre.question,
        "budget": budget,
        "must_include": [answer],
        "must_not_include": [near_miss],
        "traps": traps,
        # Every seventh case is held out and not read while tuning.
        "split": "held_out" if variant == 2 else "train",
        "tier": "ci" if variant == 0 else "full",
    }
    return case_id, documents, manifest


# -- the oracle ----------------------------------------------------------

#: Shapes that would mean a generated document carries something real. The
#: files ship inside the package, so a real address or key committed here is
#: published to everyone who installs tsumugi. The generator invents
#: everything; this checks the instruction was kept.
_FORBIDDEN = ("@", "://", "-----BEGIN", "BEGIN RSA", "password", "api_key", "sk-")


def check(directory: Path) -> list[str]:
    """Verify a case before it ships. Returns the problems it found."""
    problems: list[str] = []
    try:
        case = load_case(directory)
    except (ValueError, KeyError) as error:
        return [f"does not load: {error}"]

    # Solvable: every required fact is really in a document, and the text a
    # reader would search for is present.
    for fact_id in case.must_include:
        fact = case.facts[fact_id]
        document = case.documents[case.fact_document[fact_id]]
        if fact.span.slice(document) != fact.text:
            problems.append(f"{fact_id}: the span does not slice back to the fact")

    # Not trivial: a corpus whose only document is the answer measures nothing.
    if len(case.documents) < 2:
        problems.append("only one document; any retriever passes this")

    # Traps have to be distinguishable from the answer, or the case is asking
    # for something impossible.
    for fact_id in case.must_not_include:
        if case.facts[fact_id].text == case.facts[case.must_include[0]].text:
            problems.append(f"{fact_id} is identical to the required fact")

    for relative, text in case.documents.items():
        for shape in _FORBIDDEN:
            if shape in text:
                problems.append(f"{relative} contains {shape!r}, which may be real data")

    return problems


def write_case(
    root: Path, case_id: str, documents: dict[str, str], manifest: dict[str, object]
) -> None:
    directory = root / case_id
    if directory.exists():
        shutil.rmtree(directory)
    (directory / "corpus").mkdir(parents=True)
    for name, text in documents.items():
        (directory / "corpus" / name).write_text(text, encoding="utf-8")
    (directory / "case.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("tests/cases"))
    parser.add_argument("--per-genre", type=int, default=3)
    parser.add_argument("--check-only", action="store_true", help="verify what is already there")
    args = parser.parse_args(argv)

    if args.check_only:
        failures = 0
        for directory in sorted(args.out.iterdir()):
            if not (directory / "case.json").is_file():
                continue
            for problem in check(directory):
                print(f"{directory.name}: {problem}", file=sys.stderr)
                failures += 1
        print(f"{'FAILED' if failures else 'ok'}: {failures} problems")
        return 1 if failures else 0

    args.out.mkdir(parents=True, exist_ok=True)
    written, rejected = 0, 0
    for genre in GENRES:
        for variant in range(args.per_genre):
            case_id, documents, manifest = build_case(genre, variant)
            write_case(args.out, case_id, documents, manifest)

            # Discarded rather than shipped. A broken case fails a correct
            # implementation, and that is the expensive kind of failure.
            problems = check(args.out / case_id)
            if problems:
                for problem in problems:
                    print(f"REJECTED {case_id}: {problem}", file=sys.stderr)
                shutil.rmtree(args.out / case_id)
                rejected += 1
                continue
            written += 1

    print(f"{written} cases written to {args.out}, {rejected} rejected by the oracle")
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
