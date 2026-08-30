"""The ContextPackage: everything a model needs for one question, and an
account of where it came from and what was left behind.

A **document**, not an object (ADR-0002). JSON, portable, versioned, readable
by a program that has never heard of Python. tsumugi is the reference producer,
not required to be the only one.

The invariants are checked at construction, not on the way out. A package that
cannot be built is better than one that is built wrong and discovered later by
a consumer with no way to tell.

    the estimate never exceeds the budget
    every omission names a rule and a reason
    no omission carries the text of an item that was sent
    a token budget carries the estimator's measured error
    identical inputs produce an identical package_id

The full contract, including the JSON shape, is ``docs/context-package.md``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from .anchor import Anchor
from .budget import Budget, Unit
from .hashing import ContentHash
from .omission import Omission, OmissionRule
from .selection import ContextItem, ItemProvenance, Layer, SelectionTrace
from .span import Span

__all__ = [
    "CONTRACT",
    "SUPPORTED_CONTRACTS",
    "BudgetReport",
    "ContextPackage",
    "PackageProvenance",
    "Protection",
    "corpus_state",
]

#: **Frozen.** A field may be added; none will be removed or change meaning
#: inside version 1. A change that a consumer must notice takes a new version.
#:
#: Frozen once a second program had produced and consumed a package rather than
#: once the calendar said v0.2. The MCP server (ADR-0012) builds one in one
#: process, hands it to an agent, and verifies it in another -- through JSON,
#: with no shared objects. That round trip is the evidence ADR-0002 wanted:
#: "a class other programs can import is a different kind of object from a
#: document other programs can produce."
CONTRACT: Final = "tsumugi.context-package/1"

#: A consumer that does not recognise the contract refuses the package rather
#: than guessing at it. Fail closed.
#:
#: The draft string is still read, because packages built before the freeze
#: exist and refusing them would be discarding evidence over a version string.
#: It is not written any more.
SUPPORTED_CONTRACTS: Final = frozenset({CONTRACT, "tsumugi.context-package/1-draft"})


@dataclass(frozen=True, slots=True)
class Protection:
    """What redacted this package, and whether it can be undone.

    Present so that a verifier which sees it and holds no restorer can refuse
    loudly instead of reporting every honest citation as unsupported
    (ADR-0009). tsumugi stores the scope *identifier* only -- never the
    mapping, which would put every real value back into the index.
    """

    by: str
    scope: str
    #: ``False`` unless something says otherwise, and the default is the whole
    #: decision (ADR-0020). Getting this wrong in the ``True`` direction
    #: reports honest citations as *unsupported* -- a false accusation, and a
    #: silent one, because the output looks like a correctly-caught
    #: fabrication. Wrong in the ``False`` direction reports everything as
    #: *unverifiable*, with its reason: useless, obvious, and fixed by passing
    #: the right value.
    reversible: bool = False

    def __post_init__(self) -> None:
        if not self.by or not self.scope:
            raise ValueError("a protection record must name its redactor and its scope")


@dataclass(frozen=True, slots=True)
class BudgetReport:
    """The budget, what was spent, and how much to distrust the number."""

    budget: Budget
    estimate: int
    #: Versioned model name: a change to the estimator changes every budget
    #: decision and therefore every package_id (ADR-0003).
    estimator: str
    #: Required when the unit is tokens and the estimator is a heuristic.
    measured_error: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.estimate < 0:
            raise ValueError(f"a negative estimate of {self.estimate}")
        if not self.estimator:
            raise ValueError("a budget report must name the model that produced it")
        if self.estimate > self.budget.limit:
            raise ValueError(
                f"the package estimates {self.estimate} {self.budget.unit.value} against a "
                f"budget of {self.budget.limit}; a package over its own budget is not a package"
            )
        if self.budget.unit is Unit.TOKENS and self.measured_error is None:
            raise ValueError(
                "a token budget must carry the estimator's measured error. The core has "
                "no tokenizer, so this number is an estimate, and an estimate that does "
                "not say how wrong it is will mislead a caller exactly once. Use "
                "Budget.characters() for an exact unit, or a cost model that has been "
                "measured (ADR-0006)."
            )


@dataclass(frozen=True, slots=True)
class PackageProvenance:
    """What produced this package, over what."""

    tsumugi_version: str
    #: A hash of the corpus state the package was built against. Part of
    #: ``package_id``, which is what makes reproducibility checkable.
    corpus_state: ContentHash | None = None
    settings_hash: ContentHash | None = None
    providers: tuple[str, ...] = ()
    #: ``None`` when nothing has redacted this package.
    protection: Protection | None = None


@dataclass(frozen=True, slots=True)
class ContextPackage:
    """One question, the context selected for it, and what was left out."""

    query: str
    items: tuple[ContextItem, ...]
    #: Required, and empty only when nothing was considered and dropped.
    omissions: tuple[Omission, ...]
    budget: BudgetReport
    provenance: PackageProvenance
    instructions: Mapping[str, Any] = field(default_factory=dict)
    constraints: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] | None = None
    #: ISO 8601. Deliberately excluded from ``package_id`` -- it is the only
    #: reason a timestamp is allowed in the document at all.
    created_at: str = ""
    contract: str = CONTRACT

    def __post_init__(self) -> None:
        if self.contract not in SUPPORTED_CONTRACTS:
            raise ValueError(
                f"unrecognised contract {self.contract!r}; this tsumugi understands "
                f"{', '.join(sorted(SUPPORTED_CONTRACTS))}"
            )
        if not self.query.strip():
            raise ValueError("a package with no query answers nothing")

        spent = sum(item.cost for item in self.items)
        if spent != self.budget.estimate:
            raise ValueError(
                f"the items cost {spent} and the report says {self.budget.estimate}; "
                f"a budget that does not add up cannot be checked"
            )

        seen: set[str] = set()
        for item in self.items:
            if item.item_id in seen:
                raise ValueError(f"duplicate item id {item.item_id!r}")
            seen.add(item.item_id)

        # An omission carrying the text of something that *was* sent means the
        # two lists disagree about what happened.
        included = {(i.anchor.document_id, i.anchor.span) for i in self.items}
        for omission in self.omissions:
            if (omission.document_id, omission.span) in included:
                raise ValueError(
                    f"{omission.document_id} appears in both items and omissions at the "
                    f"same span; a package cannot both send and withhold one passage"
                )

    # -- identity --------------------------------------------------------

    @property
    def package_id(self) -> ContentHash:
        """``sha256`` over everything that determined this package.

        Excludes ``created_at``. Two runs with the same corpus, query, settings
        and version produce the same id and byte-identical output, which buys
        caching, diffing and regression tests at once (ADR-0003).
        """
        return ContentHash.of(self._canonical())

    def _canonical(self) -> str:
        payload = self.to_dict()
        payload.pop("created_at", None)
        payload.pop("package_id", None)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    # -- reading ---------------------------------------------------------

    @property
    def dropped(self) -> int:
        return len(self.omissions)

    def omitted_by(self, rule: str) -> tuple[Omission, ...]:
        return tuple(o for o in self.omissions if o.rule.value == rule)

    def why_not(self) -> str:
        """What was left out, and under which rule. The most useful thing here.

        A reader's first question about a selection is almost never about what
        came back.
        """
        if not self.omissions:
            return "Nothing was considered and dropped."
        lines = [f"{len(self.omissions)} candidates were considered and not sent:"]
        by_rule: dict[str, list[Omission]] = {}
        for omission in self.omissions:
            by_rule.setdefault(omission.rule.value, []).append(omission)
        for rule in sorted(by_rule):
            lines.append(f"  {rule} ({len(by_rule[rule])})")
            for omission in by_rule[rule]:
                lines.append(f"    {omission.describe()}")
        return "\n".join(lines)

    # -- rendering -------------------------------------------------------

    def render(self) -> str:
        """The structured prompt, as named sections.

        Sections rather than one wall of text, so that a consumer can drop or
        reorder a part without re-deriving the whole thing, and so that a model
        is told which paragraph is context and which is instruction.
        """
        blocks: list[str] = []

        role = self.instructions.get("role")
        rules = self.instructions.get("rules") or []
        if role or rules:
            body = [str(role)] if role else []
            body.extend(f"- {rule}" for rule in rules)
            blocks.append("# SYSTEM\n" + "\n".join(body))

        blocks.append(f"# TASK\n{self.query}")

        if self.items:
            rendered = []
            for item in self.items:
                header = f"[{item.item_id}] {item.describe()}"
                if item.provenance.layer.value != "fact":
                    header += f" -- {item.provenance.layer.value}"
                    if item.provenance.confidence is not None:
                        header += f", confidence {item.provenance.confidence}"
                # ADR-0008 marks redundancy and never removes it. The mark used
                # to live only in the JSON, which left the one party that could
                # act on it -- the model reading this prompt -- as the one
                # party never told. Marking a consumer cannot see is not
                # marking.
                repeats = _repeats(item)
                if repeats:
                    header += f" -- repeats {', '.join(repeats)}"
                rendered.append(f"{header}\n{item.text}")
            blocks.append("# CONTEXT\n\n" + "\n\n".join(rendered))

        # The model is told the selection has edges. It cannot see them
        # otherwise, and it will answer with the confidence of complete
        # information (ADR-0005).
        if self.omissions:
            blocks.append(
                f"# NOT INCLUDED\n{len(self.omissions)} relevant-looking passages were "
                f"considered and left out of this context. Do not assume what you have "
                f"been given is complete."
            )

        if self.constraints:
            blocks.append(
                "# CONSTRAINTS\n"
                + "\n".join(f"- {k}: {v}" for k, v in sorted(self.constraints.items()))
            )

        if self.output_schema is not None:
            blocks.append(
                "# OUTPUT_SCHEMA\n"
                + json.dumps(self.output_schema, ensure_ascii=False, indent=2, sort_keys=True)
            )

        return "\n\n".join(blocks)

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The contract's JSON shape, as plain Python.

        Without ``package_id``: the id is a hash *of* this dictionary, so
        including it here would be circular. ``to_json`` adds it.
        """
        payload: dict[str, Any] = {
            "contract": self.contract,
            "query": self.query,
            "instructions": dict(self.instructions),
            "items": [_item_to_dict(item) for item in self.items],
            "omissions": [_omission_to_dict(o) for o in self.omissions],
            "constraints": dict(self.constraints),
            "output_schema": self.output_schema,
            "budget": _budget_to_dict(self.budget),
            "provenance": _provenance_to_dict(self.provenance),
        }
        if self.created_at:
            payload["created_at"] = self.created_at
        return payload

    @classmethod
    def from_json(cls, payload: str) -> ContextPackage:
        """Read a package back. The other half of being a document.

        A contract only one program can produce is not a contract. This is what
        lets `tsumugi verify` check an answer against a package built minutes
        earlier, in another process, on another machine.

        The ``package_id`` in the payload is **checked, not trusted**: it is
        recomputed from the content, and a mismatch raises. An id that came
        along for the ride would be worse than no id, because it would look
        like a guarantee.
        """
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError(f"not a package: {error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"a package is an object, not a {type(data).__name__}")

        contract = data.get("contract")
        if contract not in SUPPORTED_CONTRACTS:
            raise ValueError(
                f"unrecognised contract {contract!r}; this tsumugi understands "
                f"{', '.join(sorted(SUPPORTED_CONTRACTS))}"
            )

        package = cls(
            query=data["query"],
            items=tuple(_item_from_dict(raw) for raw in data.get("items", [])),
            omissions=tuple(_omission_from_dict(raw) for raw in data.get("omissions", [])),
            budget=_budget_from_dict(data["budget"]),
            provenance=_provenance_from_dict(data["provenance"]),
            instructions=data.get("instructions") or {},
            constraints=data.get("constraints") or {},
            output_schema=data.get("output_schema"),
            created_at=data.get("created_at", ""),
            contract=contract,
        )

        stated = data.get("package_id")
        if stated is not None and stated != str(package.package_id):
            raise ValueError(
                f"this package claims to be {stated} and hashes to {package.package_id}; "
                f"it has been altered since it was built"
            )
        return package

    def to_json(self, *, indent: int | None = 2) -> str:
        payload = self.to_dict()
        payload["package_id"] = str(self.package_id)
        return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True)


# -- serialization helpers -----------------------------------------------


def _item_from_dict(raw: dict[str, Any]) -> ContextItem:
    anchor = raw["anchor"]
    provenance = raw["provenance"]
    selection = raw.get("selection")
    return ContextItem(
        item_id=raw["item_id"],
        text=raw["text"],
        anchor=Anchor(
            document_id=anchor["document_id"],
            span=Span(anchor["start"], anchor["end"]),
            text_hash=ContentHash.parse(anchor["text_hash"]),
            version=ContentHash.parse(anchor["document_hash"]),
        ),
        source_path=anchor.get("source_path", ""),
        section=anchor.get("section", ""),
        kind=raw.get("kind", "document_span"),
        provenance=ItemProvenance(
            layer=Layer(provenance["layer"]),
            producer=provenance["producer"],
            observed_at=provenance.get("observed_at"),
            confidence=provenance.get("confidence"),
        ),
        selection=(
            SelectionTrace(
                rank=selection["rank"],
                score=selection["score"],
                signals=tuple(selection.get("signals", ())),
            )
            if selection is not None
            else None
        ),
        cost=raw["cost"],
    )


def _omission_from_dict(raw: dict[str, Any]) -> Omission:
    anchor = raw["anchor"]
    return Omission(
        rule=OmissionRule.parse(raw["rule"]),
        reason=raw["reason"],
        document_id=anchor["document_id"],
        span=Span(anchor["start"], anchor["end"]),
        source_path=anchor.get("source_path", ""),
        score=raw.get("score"),
        cost=raw.get("cost"),
    )


def _budget_from_dict(raw: dict[str, Any]) -> BudgetReport:
    return BudgetReport(
        budget=Budget(Unit(raw["unit"]), raw["limit"]),
        estimate=raw["estimate"],
        estimator=raw["estimator"],
        measured_error=raw.get("measured_error"),
    )


def _provenance_from_dict(raw: dict[str, Any]) -> PackageProvenance:
    protection = raw.get("protection")
    return PackageProvenance(
        tsumugi_version=raw["tsumugi_version"],
        corpus_state=(ContentHash.parse(raw["corpus_state"]) if raw.get("corpus_state") else None),
        settings_hash=(
            ContentHash.parse(raw["settings_hash"]) if raw.get("settings_hash") else None
        ),
        providers=tuple(raw.get("providers", ())),
        protection=(
            Protection(
                by=protection["by"],
                scope=protection["scope"],
                # A document missing this is already non-conforming: the
                # schema requires it. All this decides is how loudly a
                # malformed input fails, and loudly is the answer (ADR-0020).
                reversible=protection.get("reversible", False),
            )
            if protection
            else None
        ),
    )


def _item_to_dict(item: ContextItem) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": item.item_id,
        "kind": item.kind,
        "text": item.text,
        "anchor": {
            "document_id": item.anchor.document_id,
            "source_path": item.source_path,
            "section": item.section,
            "start": item.anchor.span.start,
            "end": item.anchor.span.end,
            "text_hash": str(item.anchor.text_hash),
            "document_hash": str(item.anchor.version),
        },
        "provenance": {
            "layer": item.provenance.layer.value,
            "producer": item.provenance.producer,
        },
        "cost": item.cost,
    }
    if item.provenance.observed_at:
        payload["provenance"]["observed_at"] = item.provenance.observed_at
    if item.provenance.confidence is not None:
        payload["provenance"]["confidence"] = item.provenance.confidence
    if item.selection is not None:
        payload["selection"] = {
            "rank": item.selection.rank,
            "score": item.selection.score,
            "signals": list(item.selection.signals),
        }
    return payload


def _omission_to_dict(omission: Omission) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rule": omission.rule.value,
        "reason": omission.reason,
        "anchor": {
            "document_id": omission.document_id,
            "source_path": omission.source_path,
            "start": omission.span.start,
            "end": omission.span.end,
        },
    }
    if omission.score is not None:
        payload["score"] = omission.score
    if omission.cost is not None:
        payload["cost"] = omission.cost
    return payload


def _budget_to_dict(report: BudgetReport) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "unit": report.budget.unit.value,
        "limit": report.budget.limit,
        "estimate": report.estimate,
        "estimator": report.estimator,
    }
    if report.measured_error is not None:
        payload["measured_error"] = dict(report.measured_error)
    return payload


def _provenance_to_dict(provenance: PackageProvenance) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tsumugi_version": provenance.tsumugi_version,
        "providers": list(provenance.providers),
        "protection": None,
    }
    if provenance.corpus_state is not None:
        payload["corpus_state"] = str(provenance.corpus_state)
    if provenance.settings_hash is not None:
        payload["settings_hash"] = str(provenance.settings_hash)
    if provenance.protection is not None:
        payload["protection"] = {
            "by": provenance.protection.by,
            "scope": provenance.protection.scope,
            "reversible": provenance.protection.reversible,
        }
    return payload


def _repeats(item: ContextItem) -> list[str]:
    """Item ids this one was found to duplicate, from its selection signals.

    Read out of the signals rather than stored twice: the signal is what
    ``assembly`` produced and what the published document carries, and a second
    copy of the same fact is a second thing to keep in step.
    """
    if item.selection is None:
        return []
    return [
        signal.split(":", 1)[1]
        for signal in item.selection.signals
        if signal.startswith("redundant_with:")
    ]


def corpus_state(versions: Sequence[ContentHash]) -> ContentHash:
    """One hash standing for the state of every document that could be selected.

    Sorted before hashing, so the value depends on the corpus and not on the
    order the store happened to return it in (ADR-0003).
    """
    return ContentHash.of("\n".join(sorted(str(v) for v in versions)))
