"""Draft evaluation genres with a local model, for a human to review.

    python tools/draft_genres.py --language ko --count 3 --model qwen2.5:14b-instruct

Prints JSON to stdout. **Nothing here writes a fixture.** The output is read,
edited and committed by a person into ``tools/genres.json``, and
``generate_cases.py`` stays deterministic with no model anywhere near it — CI
calls nothing (ADR-0013).

The reason a model is involved at all is narrow and worth stating. Every genre
in this corpus was written by whoever was writing the code at the time, so the
vocabulary of the questions and the vocabulary of the documents came from one
head. A ranker tuned against that is tuned against one person's idea of how a
note is phrased. Asking something else for the words is not a shortcut; it is
the only cheap way to get vocabulary that was not chosen with the
implementation in mind.

What the model is *not* trusted with: correctness. Every draft is checked here
for the properties the corpus depends on — the paraphrase must share no long
substring with the question, the neighbour must differ from the subject, the
superseded answer must differ from the answer — and then read by a person
before it is committed. A draft that fails a check is printed with its reason
rather than dropped, because the failures are informative.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsumugi.errors import TsumugiError
from tsumugi.infrastructure.adapters.ollama import DEFAULT_MODEL, OllamaProvider

FIELDS = (
    "key",
    "language",
    "subject",
    "attribute",
    "answer",
    "neighbour",
    "superseded_answer",
    "heading",
    "question",
    "paraphrase",
)

_LANGUAGE_NAMES = {
    "ja": "Japanese",
    "en": "English",
    "zh": "Simplified Chinese",
    "ko": "Korean",
}

_PROMPT = """\
Invent {count} evaluation genres for a document-retrieval test corpus, in {language}.

A genre is one everyday subject somebody keeps notes about, and one factual
attribute of it. Vary the domains widely: household, medical appointments,
sports club logistics, warranty terms, allotments, transit, school admin,
recipes, hardware inventory, anything ordinary.

Return JSON and nothing else:

{{"genres": [{{
  "key": "kebab-case-identifier-in-english",
  "subject": "the thing, in {language}",
  "attribute": "a factual property of it, in {language}",
  "answer": "the value of that property, in {language}",
  "neighbour": "a DIFFERENT thing that shares the subject's vocabulary, in {language}",
  "superseded_answer": "an older, different value of the same property, in {language}",
  "heading": "a short document heading, in {language}",
  "question": "a question for the attribute, using the same words as subject and attribute",
  "paraphrase": "the SAME question asked as a person would, sharing as few words as it can"
}}]}}

Rules:
- Everything invented. No real people, companies, addresses or numbers.
- `neighbour` must be a different subject, not a synonym of `subject`.
- `superseded_answer` must be a different value from `answer`.
- `paraphrase` must not repeat the whole of `question`.
"""


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _longest_shared(left: str, right: str) -> int:
    """The longest substring the two share. The corpus's paraphrase property."""
    a, b = _fold(left), _fold(right)
    best = 0
    for start in range(len(a)):
        for end in range(start + best + 1, len(a) + 1):
            if a[start:end] in b:
                best = end - start
            else:
                break
    return best


def problems(genre: dict[str, Any], language: str) -> list[str]:
    """Everything wrong with a draft, so a reader can judge it at a glance."""
    found: list[str] = []
    for field in FIELDS:
        if field == "language":
            continue
        value = genre.get(field)
        if not isinstance(value, str) or not value.strip():
            found.append(f"missing {field}")
    if found:
        return found

    if _fold(genre["neighbour"]) == _fold(genre["subject"]):
        found.append("neighbour repeats subject")
    if _fold(genre["superseded_answer"]) == _fold(genre["answer"]):
        found.append("superseded_answer repeats answer")

    # The property the paraphrase cases exist for, stated the right way round.
    #
    # The first version of this check rejected every Chinese draft for sharing
    # four characters with the question -- which was the *subject*. Keeping the
    # subject is what a paraphrase does; a person asking about their tent still
    # says "tent". What must move is the **attribute**: `重量` -> `どれくらい
    # 重い`. Measuring the shared run measured the wrong half, and it rejected
    # the corpus's own existing genres too.
    attribute = _fold(genre["attribute"])
    if attribute and attribute in _fold(genre["paraphrase"]):
        found.append(f"paraphrase still uses the attribute {genre['attribute']!r}")

    # And it must not simply be the question again with the punctuation moved.
    shared = _longest_shared(genre["paraphrase"], genre["question"])
    if shared >= max(1, len(_fold(genre["question"])) - 2):
        found.append("paraphrase is the question")

    # The answer has to be findable from the question by the words they share.
    if _longest_shared(genre["question"], genre["subject"]) < min(2, len(genre["subject"])):
        found.append("question does not contain the subject")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", required=True, choices=sorted(_LANGUAGE_NAMES))
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--url", default=None)
    # Drafting several genres at once is a long generation; the provider's
    # default is sized for answering one question.
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    provider = OllamaProvider(
        model=args.model, timeout=args.timeout, **({"url": args.url} if args.url else {})
    )
    prompt = _PROMPT.format(count=args.count, language=_LANGUAGE_NAMES[args.language])

    try:
        answer = provider.generate(prompt)
    except TsumugiError as error:
        print(f"{error}", file=sys.stderr)
        return 1

    text = answer.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1])
    try:
        drafted = json.loads(text).get("genres", [])
    except (json.JSONDecodeError, AttributeError):
        print(f"the model did not return JSON:\n{answer}", file=sys.stderr)
        return 1

    kept = []
    for genre in drafted:
        genre["language"] = args.language
        found = problems(genre, args.language)
        if found:
            print(f"# REJECTED {genre.get('key')}: {'; '.join(found)}", file=sys.stderr)
            continue
        kept.append({field: genre[field] for field in FIELDS})

    print(json.dumps(kept, ensure_ascii=False, indent=2))
    print(f"# {len(kept)} of {len(drafted)} drafts passed the checks", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
