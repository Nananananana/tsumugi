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
    question: str = ""
    #: The same question asked the way a person asks it, sharing no contiguous
    #: phrase with the document. This is the shape the corpus had none of:
    #: every question in it was built from the subject and attribute the
    #: document uses, so every one confirmed by exact phrase and the corpus
    #: could not see that nothing else does.
    paraphrase: str = ""


#: The genres live in data rather than in this file, so that adding one is a
#: data change a contributor can make without reading the generator -- and so
#: that the vocabulary is not limited to whatever the person writing the
#: generator happened to think of.
#:
#: `tools/draft_genres.py` drafts new ones with a local model and checks them
#: against the properties the corpus depends on. A person reads them before
#: they land here. **No model runs when cases are generated**, and none runs in
#: CI (ADR-0013): the fixtures are committed and the generator is deterministic.
GENRES_PATH = Path(__file__).resolve().parent / "genres.json"


def load_genres(path: Path = GENRES_PATH) -> tuple[Genre, ...]:
    """Every genre, in file order.

    File order is the order, and the order matters: shapes and splits are
    assigned from a genre's index, so reordering this file reshuffles the
    corpus. That is a deliberate cost of keeping the assignment deterministic
    without a seed -- adding a genre at the end changes nothing before it.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("format_version")
    if version != 1:
        raise ValueError(f"{path}: unknown format_version {version!r}")

    genres: list[Genre] = []
    seen: set[tuple[str, str]] = set()
    for row in payload["genres"]:
        genre = Genre(**row)
        # Language and key together, because a case id is built from both and
        # `medical-appointment` is a reasonable genre in every language.
        identity = (genre.language, genre.key)
        if identity in seen:
            raise ValueError(f"{path}: two {genre.language} genres called {genre.key!r}")
        seen.add(identity)
        genres.append(genre)
    if not genres:
        raise ValueError(f"{path}: no genres")
    return tuple(genres)


GENRES: tuple[Genre, ...] = load_genres()

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
    "zh": ("这一条在上次盘点时复核过。\n负责人轮换，交接时确认一次。\n详细的经过另有记录。\n"),
    "ko": (
        "이 항목은 지난 재고 조사에서 검토했다.\n"
        "담당은 돌아가며 맡고, 인수인계 때 한 번 확인한다.\n"
        "자세한 경위는 별도 기록에 남겨 두었다.\n"
    ),
}


#: The shapes a document can take. Every document in this corpus used to be
#: front matter, one heading, one sentence, one filler block -- so a ranker
#: could have keyed on "the fact is the first sentence after an H1", scored
#: 100% recall, and learned nothing that survives a real notes folder.
#:
#: Borrowed in spirit from `mamori`, whose evaluation data is split by the
#: *kind* of text as well as by language: fragments, documents, conversations
#: and tool payloads stress different things, and one set measures one of them.
SHAPES: tuple[str, ...] = (
    "article",
    "bare",
    "buried",
    "table",
    "bullets",
    "nested",
    "log",
    "trailing",
)

_TABLE_HEAD = {
    "ja": "| 項目 | 内容 |\n|---|---|\n",
    "en": "| Field | Value |\n|---|---|\n",
    "zh": "| 项目 | 内容 |\n|---|---|\n",
    "ko": "| 항목 | 내용 |\n|---|---|\n",
}
_SUBHEADS = {
    "ja": ("詳細", "現状"),
    "en": ("Detail", "Current"),
    "zh": ("详细", "现状"),
    "ko": ("상세", "현황"),
}
_LOG_LINES = {
    "ja": ("10:02 佐藤: 前回の分は片付いた。", "10:05 田中: "),
    "en": ("10:02 sam: the previous batch is cleared.", "10:05 alex: "),
    "zh": ("10:02 张: 上一批已经处理完。", "10:05 李: "),
    "ko": ("10:02 김: 지난 건은 정리했다.", "10:05 박: "),
}


def _shaped(shape: str, *, heading: str, sentence: str, language: str) -> str:
    """One marked sentence, placed the way that shape places it.

    ``sentence`` already carries its markup and its full stop. Nothing here
    touches it: a shape is what *surrounds* a fact, so where the fact sits
    cannot change what its span covers.
    """
    filler = _FILLER[language]
    if shape == "article":
        return f"---\ntitle: {heading}\n---\n\n# {heading}\n\n{sentence}\n\n{filler}"
    if shape == "bare":
        # No front matter, no heading. A note somebody typed into a file.
        return f"{sentence}\n{filler}"
    if shape == "buried":
        # Mid-paragraph, no blank line on either side. Nothing marks it out.
        lead, rest = filler.split("\n", 1)
        return f"# {heading}\n\n{lead}{sentence}{rest}"
    if shape == "table":
        return f"# {heading}\n\n{_TABLE_HEAD[language]}| {heading} | {sentence} |\n\n{filler}"
    if shape == "bullets":
        lines = [line for line in filler.split("\n") if line]
        head = "\n".join(f"- {line}" for line in lines[:1])
        tail = "\n".join(f"- {line}" for line in lines[1:])
        return f"# {heading}\n\n{head}\n- {sentence}\n{tail}\n"
    if shape == "nested":
        first, second = _SUBHEADS[language]
        return f"# {heading}\n\n## {first}\n\n{filler}\n### {second}\n\n{sentence}\n"
    if shape == "log":
        before, speaker = _LOG_LINES[language]
        return f"# {heading}\n\n{before}\n{speaker}{sentence}\n{filler}"
    if shape == "trailing":
        # The last line, with no trailing newline. The very end of a file is
        # where an off-by-one in offset arithmetic lives.
        return f"# {heading}\n\n{filler}\n{sentence}"
    raise ValueError(f"unknown shape {shape!r}")


def _shape_for(genre: Genre, variant: int, role: str) -> str:
    """Which shape a document takes, deterministically.

    Keyed on the role as well as the variant, so an answer and its adversaries
    never share a shape in one case. Otherwise "the fact is in the
    differently-shaped document" becomes a signal, and the corpus would be
    measuring that instead of retrieval.
    """
    offset = {"answer": 0, "superseded": 3, "near-miss": 5, "duplicate": 6}.get(role, 1)
    return SHAPES[(GENRES.index(genre) + variant * 2 + offset) % len(SHAPES)]


#: How a claim is written, per language. Keyed rather than branched: the first
#: version treated Chinese as Japanese and would have emitted
#: ``家庭预算の每月的食品开支は1500元`` -- Japanese particles around Chinese
#: words, which is not a sentence in either language and would have measured
#: a tokenizer against text no corpus contains.
_CLAIM = {
    "ja": "{subject}の{attribute}は{value}",
    "zh": "{subject}的{attribute}是{value}",
    "ko": "{subject}의 {attribute}은 {value}",
    "en": "The {attribute} of {subject} is {value}",
}

#: The full stop that ends a sentence in each script.
_STOP = {"ja": "。", "zh": "。", "ko": ".", "en": "."}


def _claim(genre: Genre, subject: str, value: str) -> str:
    """One factual sentence, without markup. Also the shape of a question."""
    return _CLAIM[genre.language].format(subject=subject, attribute=genre.attribute, value=value)


def _answer_sentence(genre: Genre) -> str:
    """The sentence the answer document states, without its markup.

    Left behind when `_claim` arrived, and still wrapping every non-Japanese
    genre in an English frame: a Chinese case stated
    ``The 每月的食品开支 of 家庭预算 is 1500元``, which is not a sentence
    anybody writes and which no Chinese question can phrase-match. The
    mixed-script case is about scripts meeting inside a *document*, not inside
    a sentence nobody would write.
    """
    return _claim(genre, genre.subject, genre.answer)


def _sentence(genre: Genre, fact_id: str, subject: str, value: str) -> str:
    """One marked claim, in the genre's language. The unit every shape places."""
    return f"{{{{F:{fact_id}}}}}{_claim(genre, subject, value)}{{{{/F}}}}{_STOP[genre.language]}"


#: A question that uses the document's own words, per language. Derived rather
#: than authored, because it has to phrase-match the claim above -- and a
#: drafted question that does not is an accidental paraphrase case, which
#: measures the wrong thing while looking like it measures the right one.
_ASKING = {
    "ja": "{subject}の{attribute}は",
    "zh": "{subject}的{attribute}是多少",
    "ko": "{subject}의 {attribute}은",
    "en": "what is the {attribute} of {subject}",
}


def _exact_question(genre: Genre) -> str:
    """The question that shares the document's phrasing.

    A genre may state its own instead -- the English genres do, because
    "how many nodes does the staging cluster have" is what somebody would type
    and still confirms on word runs. Where the field is empty this is used, so
    a genre drafted by a model cannot accidentally ship a question its own
    document does not answer.
    """
    return genre.question or _ASKING[genre.language].format(
        subject=genre.subject, attribute=genre.attribute
    )


def _answer_document(genre: Genre, fact_id: str, variant: int = 0) -> str:
    return _shaped(
        _shape_for(genre, variant, "answer"),
        heading=genre.heading,
        sentence=_sentence(genre, fact_id, genre.subject, genre.answer),
        language=genre.language,
    )


def _superseded_document(genre: Genre, fact_id: str, variant: int = 0) -> str:
    """Nearly identical to the answer. The differing value is the correction.

    The trap most worth getting right and the easiest to get wrong: two
    passages that are nearly identical are usually nearly identical *because*
    the difference is a correction.
    """
    note = {
        "ja": "※この記録は古い。改訂版を参照すること。",
        "zh": "※此记录已过期，请参阅修订版。",
        "ko": "※이 기록은 오래되었다. 개정판을 참조할 것.",
    }.get(genre.language, "NOTE: this record is out of date; see the revision.")
    body = _shaped(
        _shape_for(genre, variant, "superseded"),
        heading=genre.heading,
        sentence=_sentence(genre, fact_id, genre.subject, genre.superseded_answer),
        language=genre.language,
    )
    return f"{body}\n{note}\n"


def _near_miss_document(genre: Genre, fact_id: str, variant: int = 0) -> str:
    """Same vocabulary, different subject. The confirmation stage's exam."""
    return _shaped(
        _shape_for(genre, variant, "near-miss"),
        heading=genre.heading,
        sentence=_sentence(genre, fact_id, genre.neighbour, genre.superseded_answer),
        language=genre.language,
    )


#: Per language, and keyed rather than branched, so adding a language is a
#: table entry and not an `if`. A missing key raises here rather than quietly
#: producing an English document with a Korean subject in it.
_COPY_HEAD = {
    "ja": "（控え）\n\n過去の記録から転記。\n\n",
    "en": " (copy)\n\nTranscribed from an earlier record.\n\n",
    "zh": "（副本）\n\n从早前的记录转抄。\n\n",
    "ko": " (사본)\n\n이전 기록에서 옮겨 적음.\n\n",
}
_APPENDIX = {
    "ja": ("補足", "の運用について、過去の経緯を並べておく。"),
    "en": (", appendix", " has been handled before, in outline."),
    "zh": ("补充", "的运作情况，把过去的经过列在这里。"),
    "ko": ("보충", "의 운용에 대해, 지난 경위를 적어 둔다."),
}


def _bulk_document(genre: Genre, n: int) -> str:
    """Competition, so that a budget can actually bind."""
    label, sentence = _APPENDIX[genre.language]
    if genre.language == "en":
        head = f"# {genre.heading}{label} {n}\n\nBackground on how {genre.subject}{sentence}\n"
    else:
        head = f"# {genre.heading} {label} {n}\n\n{genre.subject}{sentence}\n"
    return f"{head}{_FILLER[genre.language] * 3}"


def _duplicate_document(genre: Genre, fact_id: str, answer_text: str) -> str:
    """A verbatim copy of the answer, in another file.

    The trap `redundant_candidate` actually means. It replaces the expectation
    that a *superseded* document would be caught as a duplicate, which
    measurement showed is not detectable by character overlap and is not what
    the rule means (ADR-0015).
    """
    head = f"# {genre.heading}{_COPY_HEAD[genre.language]}"
    return f"{head}{{{{F:{fact_id}}}}}{answer_text}{{{{/F}}}}\n\n{_FILLER[genre.language]}"


#: Questions no document in the corpus answers. Kept in the same subject area
#: as the genre so that retrieval *will* propose documents -- a question about
#: an unrelated topic would return nothing for the boring reason.
_UNANSWERABLE = {
    "ja": "の保証期間は何年か",
    "en": "what is the warranty period of ",
    "zh": "的保修期是几年",
    "ko": "의 보증 기간은 몇 년인가",
}


def _unanswerable_question(genre: Genre) -> str:
    if genre.language == "en":
        return f"{_UNANSWERABLE['en']}{genre.subject}"
    return f"{genre.subject}{_UNANSWERABLE[genre.language]}"


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
        "current.md": _answer_document(genre, "answer", 4),
        "neighbour.md": _near_miss_document(genre, "near-miss", 4),
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
        "current.md": _answer_document(genre, "answer", 5),
        "neighbour.md": _near_miss_document(genre, "near-miss", 5),
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
        "current.md": _answer_document(genre, "answer", 6),
        "drifting.md": _superseded_document(genre, "drifted", 6),
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
        "current.md": _answer_document(genre, "answer", 7),
        "neighbour.md": _near_miss_document(genre, "near-miss", 7),
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
            _FILLER[genre.language],
            _FILLER["en"],
        ]
    )
    documents = {
        "mixed.md": body,
        "neighbour.md": _near_miss_document(genre, "near-miss", 3),
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
_QUESTION_MARK = {"ja": "？", "en": "?", "mixed": "?", "zh": "？", "ko": "?"}


def _ask(genre: Genre, variant: int) -> str:
    """The genre's question, punctuated on every other variant."""
    asked = _exact_question(genre)
    if variant % 2 == 0:
        return asked
    return asked + _QUESTION_MARK.get(genre.language, "?")


def build_case(genre: Genre, variant: int) -> tuple[str, dict[str, str], dict[str, object]]:
    """One case: its id, its marked-up documents, and its manifest."""
    case_id = f"{genre.language}-{genre.key}-{variant:02d}"
    answer, superseded, near_miss = "answer", "superseded", "near-miss"

    copy = "duplicate"
    answer_text = _answer_sentence(genre)

    documents = {
        "current.md": _answer_document(genre, answer, variant),
        "older.md": _superseded_document(genre, superseded, variant),
        "neighbour.md": _near_miss_document(genre, near_miss, variant),
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
        #
        # 3.4x: room for three of the four competing passages, and not the
        # fourth. It was 2.4x, which fit two -- and that turned out to be
        # asking tsumugi to rank the current answer above the superseded one,
        # which ADR-0015 measured and refuses to do. The case passed anyway,
        # for a reason that had nothing to do with its subject: every answer
        # document carried front matter repeating its heading, so it won on
        # bm25. Varying document shape removed the crutch and the case failed,
        # which is the corpus catching an ill-posed case rather than a defect.
        #
        # The intent survives: one candidate is still squeezed out and still
        # has to be reported by name, which is what ADR-0005 is about.
        budget = {"unit": "characters", "limit": int(len(answer_text) * 3.4)}
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
