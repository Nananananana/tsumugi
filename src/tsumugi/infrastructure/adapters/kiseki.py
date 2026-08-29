"""The `kiseki` adapter: a past, turned into a document you can anchor into.

`kiseki` reads a photo timeline as an interest profile. Its **export** is the
only thing it ever prepares for the world outside the machine, and this reads
that — never its database. Coupling to a published contract rather than a
schema is the whole difference between an adapter and a reach-in.

**It imports nothing.** The export is JSON with a documented shape, so this
needs neither `kiseki` installed nor a version of it agreed. A file is enough.

## What the export cannot give, and what follows

`kiseki` deliberately exports **no evidence references** — no photographs, no
coordinates, no timestamps, no identifiers. That is right for a library about
somebody's movements, and it collides with tsumugi's first rule: a
`ContextItem` needs an anchor, and an anchor needs a document.

The collision resolves the honest way round. The export **is** a document. An
interest is anchored into it, and what the package then claims is *"the kiseki
export of 2026-08-30 said this, here"* — which is true, checkable and traceable.
It does not claim "your photographs say this", which would be neither.

## And the layering survives

Every interest carries a confidence, so every interest is an
``interpretation`` and says so
([ADR 0002](../../../docs/adr/0002-the-context-package-is-a-document.md),
`kiseki`'s own constitution). A reading of somebody's photographs does not
become a fact by crossing a library boundary — that would be laundering, and
the rendered prompt labels it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...errors import IngestionError

__all__ = ["EXPORT_SCHEMA", "KisekiExport", "read_export", "render_export"]

#: The contract this reads. An export naming anything else is refused rather
#: than parsed hopefully.
EXPORT_SCHEMA = "kiseki-interest-export"
SUPPORTED_VERSIONS = frozenset({1})


@dataclass(frozen=True, slots=True)
class KisekiExport:
    """One export, read and checked."""

    exported_on: str
    #: ``topic -> (score, confidence, first_seen, last_seen)``, best first.
    interests: tuple[dict[str, Any], ...]
    stages: Mapping[str, str]
    version: int = 1

    @property
    def producer(self) -> str:
        return f"kiseki-interest-export/{self.version}"


def read_export(payload: str) -> KisekiExport:
    """Read an export, refusing anything it does not recognise.

    Fail closed. A consumer that cannot verify the shape refuses it rather than
    guessing — the same rule the ContextPackage contract applies to itself.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise IngestionError(f"not a kiseki export: {error}") from error
    if not isinstance(data, dict):
        raise IngestionError(f"a kiseki export is an object, not a {type(data).__name__}")

    schema = data.get("schema")
    if schema != EXPORT_SCHEMA:
        raise IngestionError(f"unrecognised export schema {schema!r}; this reads {EXPORT_SCHEMA!r}")
    version = data.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise IngestionError(
            f"kiseki export version {version!r} is not one this understands "
            f"({', '.join(str(v) for v in sorted(SUPPORTED_VERSIONS))})"
        )

    interests = data.get("interests") or []
    if not isinstance(interests, list):
        raise IngestionError("'interests' must be a list")
    for interest in interests:
        if not isinstance(interest, dict) or "topic" not in interest:
            raise IngestionError("every interest needs a topic")
        if not isinstance(interest.get("confidence"), int | float):
            # Without it the interest cannot enter a package: an interpretation
            # with no confidence is an opinion wearing a fact's clothes.
            raise IngestionError(
                f"interest {interest.get('topic')!r} has no confidence, so it cannot "
                f"be carried as an interpretation"
            )

    stages = {
        str(entry["topic"]): str(entry["stage"])
        for entry in (data.get("stages") or [])
        if isinstance(entry, dict) and "topic" in entry and "stage" in entry
    }
    return KisekiExport(
        exported_on=str(data.get("exported_on", "")),
        interests=tuple(interests),
        stages=stages,
        version=int(version),
    )


def render_export(export: KisekiExport) -> tuple[str, dict[str, str]]:
    """Turn an export into a document, and the metadata that labels it.

    Returns the text and the metadata a `Document` should carry. Ingest it like
    any other document — it is searchable, anchorable and traceable, and every
    passage taken from it is labelled an interpretation with its confidence.

    Each interest is one line, so an anchor into it covers exactly one claim.
    The header states where it came from and when, because a package built from
    this should be able to say *which* export it read.
    """
    lines = [
        "# kiseki interest export",
        "",
        f"Interests read from a photo timeline by kiseki, exported {export.exported_on}.",
        "Every line below is an interpretation with a confidence, not an observation.",
        "",
    ]
    for interest in export.interests:
        topic = str(interest["topic"])
        stage = export.stages.get(topic)
        seen = ""
        if interest.get("first_seen") and interest.get("last_seen"):
            seen = f", seen {interest['first_seen']} to {interest['last_seen']}"
        lines.append(
            f"- {topic}: confidence {float(interest['confidence']):.2f}"
            f"{seen}{f', {stage}' if stage else ''}"
        )

    metadata = {
        # Read by build_context, which is how the layering survives the
        # crossing without the application layer knowing what kiseki is.
        "layer": "interpretation",
        "producer": export.producer,
        "observed_at": export.exported_on,
        "title": "kiseki interest export",
    }
    return "\n".join(lines) + "\n", metadata
