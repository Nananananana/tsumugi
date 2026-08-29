"""What a package tells the model, as data.

`proposals/0002` demoted prompt templates and named the condition for building
them: *when a second use needs a second shape*. It has. `ask` needs an answer
in a machine-readable form, because there is nothing to verify otherwise, and a
reader pasting a package into a chat window does not.

For a while `ask` got that by appending a paragraph to `render()`. That worked
and was wrong in a way worth naming: **the package then no longer described
what was sent.** A package is the record of a prompt — the ledger stores its
id, `--json` publishes it, and a reader is invited to look at exactly what is
about to go. A prompt with an extra paragraph stapled on afterwards makes all
three of those slightly false, and "slightly false" is the failure mode this
whole library exists to avoid.

So the instruction set is a parameter, both sets live here, and
``package.render()`` is the entire prompt again. The cost is that
``package_id`` now distinguishes a package built for a human from one built for
a model — which is correct, because they are different prompts, and an id that
called them the same would be the thing that is wrong.

Two sets, not a template language. A template language is a thing to maintain
before anyone has needed the second template; two named dictionaries are not.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["ANSWERING", "ANSWER_SCHEMA", "DEFAULT"]

#: What a package says when nobody has said otherwise. A person reading this in
#: a chat window is the assumed consumer: no output format, because they can
#: see the answer, and no JSON, because they would have to read it.
DEFAULT: Final[dict[str, Any]] = {
    "role": "Answer the question using only the context provided below.",
    "rules": [
        # ADR-0004: the model quotes, tsumugi resolves the offsets. Asking for
        # positions produces coordinates that are plausible and wrong.
        "Quote the exact text you rely on. Do not report character offsets.",
        "If the context does not answer the question, say so plainly.",
        "Context marked as an interpretation is a reading, not a fact.",
    ],
}

#: What a package says when a program is going to check the answer.
#:
#: The third and fourth rules were earned. The first version of this said only
#: "quote exactly from the context", and qwen2.5:14b answered a Japanese
#: question perfectly and cited ``notes/持ち物リスト.md (持ち物リスト（控え）)``
#: -- the header line above the passage. Which is what "citation" means
#: everywhere else: name the source. Every claim reported unsupported, and the
#: answer was right.
ANSWERING: Final[dict[str, Any]] = {
    "role": DEFAULT["role"],
    "rules": [
        *DEFAULT["rules"],
        "A citation is a span of text copied out of a passage, character for "
        "character. It is not a filename, not a heading, and not a [c1] label.",
        "Copy from the lines underneath a header, never the header itself. "
        "Given\n"
        "      [c1] notes/gear.md (Gear)\n"
        "      The tent weighs 2.4kg.\n"
        "    cite from the second line, e.g. 'weighs 2.4kg'.",
        "Do not paraphrase inside a citation. A citation that is nearly right is wrong.",
        "If the context does not answer the question, say so in a claim with no citations.",
    ],
}

#: The shape an answer must take for ``verify_answer`` to have anything to
#: check. Rendered into the package's own ``# OUTPUT_SCHEMA`` section, so the
#: prompt and the published document say the same thing by construction.
ANSWER_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "citations"],
                "properties": {
                    "text": {"type": "string", "description": "one statement"},
                    "citations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "text copied from a passage, character for character",
                    },
                },
            },
        }
    },
}
