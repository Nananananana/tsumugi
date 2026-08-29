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
    #: The same question asked the way a person asks it, sharing no contiguous
    #: phrase with the document. This is the shape the corpus had none of:
    #: every question in it was built from the subject and attribute the
    #: document uses, so every one confirmed by exact phrase and the corpus
    #: could not see that nothing else does.
    paraphrase: str = ""


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
        paraphrase="テントはどれくらい重い",
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
        paraphrase="有給は何日もらえる",
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
        paraphrase="サンプルは何度で保管する",
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
        paraphrase="生地はどのくらい休ませる",
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
        paraphrase="次の会議はいつ",
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
        paraphrase="球根はいつ植える",
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
        paraphrase="how big is the staging cluster",
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
        paraphrase="how much warning must be given",
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
        paraphrase="what time does the earliest boat leave",
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
        paraphrase="how does the retry policy wait between attempts",
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


def _answer_sentence(genre: Genre) -> str:
    """The sentence the answer document states, without its markup."""
    if genre.language == "ja":
        return f"{genre.subject}の{genre.attribute}は{genre.answer}"
    return f"The {genre.attribute} of {genre.subject} is {genre.answer}"


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


def _duplicate_document(genre: Genre, fact_id: str, answer_text: str) -> str:
    """A verbatim copy of the answer, in another file.

    The trap `redundant_candidate` actually means. It replaces the expectation
    that a *superseded* document would be caught as a duplicate, which
    measurement showed is not detectable by character overlap and is not what
    the rule means (ADR-0015).
    """
    if genre.language == "ja":
        head = f"# {genre.heading}（控え）\n\n過去の記録から転記。\n\n"
    else:
        head = f"# {genre.heading} (copy)\n\nTranscribed from an earlier record.\n\n"
    return f"{head}{{{{F:{fact_id}}}}}{answer_text}{{{{/F}}}}\n\n{_FILLER[genre.language]}"


#: Questions no document in the corpus answers. Kept in the same subject area
#: as the genre so that retrieval *will* propose documents -- a question about
#: an unrelated topic would return nothing for the boring reason.
_UNANSWERABLE = {
    "ja": "の保証期間は何年か",
    "en": "what is the warranty period of ",
}


def _unanswerable_question(genre: Genre) -> str:
    if genre.language == "ja":
        return f"{genre.subject}{_UNANSWERABLE['ja']}"
    return f"{_UNANSWERABLE['en']}{genre.subject}"


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


#: Alternating, so that a threshold tuned on the train half can be reported on
#: a held-out half. A trap kind that lives entirely in one split cannot tell
#: you whether a number was fitted to the cases it came from.
_PARAPHRASE_SPLIT = {True: "train", False: "held_out"}


def build_paraphrase_case(genre: Genre) -> tuple[str, dict[str, str], dict[str, object]]:
    """The same question, asked the way a person asks it.

    Every other case in this corpus was generated from the subject and
    attribute the document uses, so every question shared a contiguous phrase
    with its answer -- and confirmation is a phrase match. Seventy cases at
    100% recall could not see that a Japanese question phrased any other way
    finds nothing at all.

    The documents are unchanged. Only the wording of the question moves, which
    is what makes this measure the confirmation stage rather than the ranker.
    """
    case_id = f"{genre.language}-{genre.key}-paraphrase"
    documents = {
        "current.md": _answer_document(genre, "answer"),
        "neighbour.md": _near_miss_document(genre, "near-miss"),
    }
    manifest: dict[str, object] = {
        "case_id": case_id,
        "genre": genre.key,
        "language": genre.language,
        "question": genre.paraphrase + _QUESTION_MARK.get(genre.language, "?"),
        "budget": {"unit": "characters", "limit": 1200},
        "must_include": ["answer"],
        "must_not_include": ["near-miss"],
        "traps": {"near-miss": {"kind": "lexical_near_miss"}},
        "split": _PARAPHRASE_SPLIT[GENRES.index(genre) % 2 == 0],
        "tier": "full",
    }
    return case_id, documents, manifest


def build_absent_case(genre: Genre) -> tuple[str, dict[str, str], dict[str, object]]:
    """A question the corpus does not answer.

    The documents are all about the right subject, so retrieval proposes them
    and confirmation has real work to do. Assembling a plausible-looking
    package for a question nothing answers is the failure an evidence system
    exists to avoid, and nothing else in the corpus tests it.
    """
    case_id = f"{genre.language}-{genre.key}-absent"
    documents = {
        "current.md": _answer_document(genre, "answer"),
        "neighbour.md": _near_miss_document(genre, "near-miss"),
    }
    manifest: dict[str, object] = {
        "case_id": case_id,
        "genre": genre.key,
        "language": genre.language,
        "question": _unanswerable_question(genre),
        "budget": {"unit": "characters", "limit": 1200},
        "must_include": [],
        "must_not_include": [],
        "traps": {"answer": {"kind": "absent_answer"}},
        "split": "train",
        "tier": "ci",
    }
    return case_id, documents, manifest


def build_stale_case(genre: Genre) -> tuple[str, dict[str, str], dict[str, object]]:
    """A document edited after it was indexed.

    Exercises ADR-0010 through the whole pipeline: the evidence was true in the
    version that was read, the file has moved on, and the package has to say so
    rather than offering it as current or dropping it without a word.
    """
    case_id = f"{genre.language}-{genre.key}-stale"
    documents = {
        "current.md": _answer_document(genre, "answer"),
        "drifting.md": _superseded_document(genre, "drifted"),
    }
    if genre.language == "ja":
        edited = (
            f"# {genre.heading}\n\nこの記録は全面的に書き直された。以前の内容は残っていない。\n"
        )
    else:
        edited = (
            f"# {genre.heading}\n\nThis record was rewritten. "
            f"Nothing of the earlier text remains here.\n"
        )
    manifest: dict[str, object] = {
        "case_id": case_id,
        "genre": genre.key,
        "language": genre.language,
        "question": _ask(genre, 1),
        "budget": {"unit": "characters", "limit": 1200},
        "must_include": ["answer"],
        "must_not_include": [],
        "traps": {"drifted": {"kind": "stale_anchor", "expect_omission_rule": "stale_anchor"}},
        "edit_after_ingest": {"drifting.md": edited},
        "split": "train",
        "tier": "ci",
    }
    return case_id, documents, manifest


def build_squeezed_out_case(genre: Genre) -> tuple[str, dict[str, str], dict[str, object]]:
    """A budget too small for the answer itself.

    Every other case has the answer fitting, so nothing tested the thing
    ADR-0005 is most about: a passage that bears on the question and does not
    fit has to be *reported*, by name and under the rule that dropped it. A
    package that returned nothing and said nothing would pass every other case
    in this corpus.
    """
    case_id = f"{genre.language}-{genre.key}-squeezed"
    documents = {
        "current.md": _answer_document(genre, "answer"),
        "neighbour.md": _near_miss_document(genre, "near-miss"),
    }
    manifest: dict[str, object] = {
        "case_id": case_id,
        "genre": genre.key,
        "language": genre.language,
        "question": _ask(genre, 0),
        # Smaller than the answer passage, so nothing at all fits.
        "budget": {"unit": "characters", "limit": 5},
        "must_include": [],
        "must_not_include": ["near-miss"],
        "traps": {
            "answer": {"kind": "budget_squeeze", "expect_omission_rule": "budget_exhausted"},
            "near-miss": {"kind": "lexical_near_miss"},
        },
        "split": "train",
        "tier": "ci",
    }
    return case_id, documents, manifest


def build_mixed_script_case(genre: Genre) -> tuple[str, dict[str, str], dict[str, object]]:
    """Japanese prose, English terms and a code block in one document.

    Exercises the two places a script boundary matters: tokenization, where a
    Latin run must not be cut into character bigrams (ADR-0007), and cost,
    where a kanji is six Latin characters' worth (ADR-0006).
    """
    case_id = f"{genre.language}-{genre.key}-mixed"
    answer = _answer_sentence(genre)
    fence = "```"
    body = "\n".join(
        [
            f"# {genre.heading} / Notes",
            "",
            "運用メモ。The current setting is recorded below, in `config.toml`.",
            "",
            f"{fence}toml",
            f"[{genre.key}]",
            "reviewed = true",
            'owner = "rotating"',
            fence,
            "",
            f"{{{{F:answer}}}}{answer}{{{{/F}}}}。See also the appendix for background.",
            "",
            _FILLER["ja"],
            _FILLER["en"],
        ]
    )
    documents = {
        "mixed.md": body,
        "neighbour.md": _near_miss_document(genre, "near-miss"),
    }
    manifest: dict[str, object] = {
        "case_id": case_id,
        "genre": genre.key,
        "language": "mixed",
        "question": _ask(genre, 1),
        "budget": {"unit": "tokens", "limit": 400},
        "must_include": ["answer"],
        "must_not_include": [],
        "traps": {"answer": {"kind": "mixed_script"}, "near-miss": {"kind": "lexical_near_miss"}},
        "split": "train",
        "tier": "ci",
    }
    return case_id, documents, manifest


#: People type questions with question marks, and until 2026-08-30 no case did
#: -- so the corpus never noticed that a trailing ``?`` was part of the phrase
#: confirmation looked for, and `tsumugi context "テントの重量は?"` returned an
#: empty package. Applied by variant so that punctuated and bare questions both
#: stay covered, and deterministically, because nothing here uses a seed.
_QUESTION_MARK = {"ja": "？", "en": "?", "mixed": "?"}


def _ask(genre: Genre, variant: int) -> str:
    """The genre's question, punctuated on every other variant."""
    if variant % 2 == 0:
        return genre.question
    return genre.question + _QUESTION_MARK.get(genre.language, "?")


def build_case(genre: Genre, variant: int) -> tuple[str, dict[str, str], dict[str, object]]:
    """One case: its id, its marked-up documents, and its manifest."""
    case_id = f"{genre.language}-{genre.key}-{variant:02d}"
    answer, superseded, near_miss = "answer", "superseded", "near-miss"

    copy = "duplicate"
    answer_text = _answer_sentence(genre)

    documents = {
        "current.md": _answer_document(genre, answer),
        "older.md": _superseded_document(genre, superseded),
        "neighbour.md": _near_miss_document(genre, near_miss),
        "copy.md": _duplicate_document(genre, copy, answer_text),
    }

    # A verbatim copy is what `redundant_candidate` means, and it is
    # detectable: 1.000 containment against a 0.75 threshold. But an
    # *omission* is only expected where the budget actually binds -- with room
    # for both, a marked duplicate is still sent, which is the whole of
    # "lowers priority, does not veto" (ADR-0008). Expecting an omission on a
    # loose budget would be asserting the opposite of the decision.
    squeezed = variant == 1
    traps: dict[str, object] = {
        copy: (
            {"kind": "near_duplicate", "expect_omission_rule": "redundant_candidate"}
            if squeezed
            else {"kind": "near_duplicate"}
        ),
        # A superseded version is NOT detectable by character overlap, and
        # expecting it under redundant_candidate was wrong (ADR-0015). It stays
        # as a planted adversary with no expected rule: it must simply not
        # displace the current answer.
        superseded: {"kind": "superseded"},
        near_miss: {"kind": "lexical_near_miss"},
    }

    # Variant 1 squeezes the budget: the required fact has to survive
    # competition, and everything dropped has to name the right rule.
    if squeezed:
        for n in range(4):
            documents[f"appendix-{n}.md"] = _bulk_document(genre, n)
        # Scaled to the content, not a constant. A Japanese answer is about
        # sixteen characters and an English one about sixty-six, so a fixed
        # character budget squeezes one language and not the other -- and a
        # "budget squeeze" case that does not squeeze measures nothing.
        # 2.4x leaves room for roughly two items and refuses a third.
        budget = {"unit": "characters", "limit": int(len(answer_text) * 2.4)}
    else:
        budget = {"unit": "characters", "limit": 1200}

    manifest: dict[str, object] = {
        "case_id": case_id,
        "genre": genre.key,
        "language": genre.language,
        "question": _ask(genre, variant),
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
    # for something impossible. A case with no required fact -- a budget
    # squeeze, where the answer is meant to be reported rather than included --
    # has nothing to compare against, and asking anyway crashed the oracle.
    for required in case.must_include[:1]:
        for fact_id in case.must_not_include:
            if case.facts[fact_id].text == case.facts[required].text:
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
    built: list[tuple[str, dict[str, str], dict[str, object]]] = []
    for genre in GENRES:
        built.extend(build_case(genre, variant) for variant in range(args.per_genre))
        # One of each of the two traps that need their own case shape: a
        # question nothing answers, and a document edited after ingest.
        built.append(build_absent_case(genre))
        built.append(build_stale_case(genre))
        built.append(build_squeezed_out_case(genre))
        built.append(build_mixed_script_case(genre))
        built.append(build_paraphrase_case(genre))

    for case_id, documents, manifest in built:
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
