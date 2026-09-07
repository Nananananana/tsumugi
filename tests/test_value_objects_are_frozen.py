"""Every dataclass in the package is frozen and slotted, or says why not.

This started as the same test written five times. `python tools/mutate.py`
kept reporting the same survivor in a different file: flip `frozen=True` or
`slots=True` on `Lead`, `IndexSummary`, `Anchor`, `Resolution`, `Claim`,
`Citation`, `Located`, `VerificationReport` — and nothing objected, each time.

Writing an eighth per-class test would have been the wrong answer twice over:
it leaves the ninth class undefended, and it says nothing about what the
library *is*. **Almost everything here is a value**, and a value that can be
edited after it is handed over is not one:

- an `Anchor` is the proof a citation points where it says. Rewrite its span
  and a `Resolution` reports RESOLVED about text it no longer describes;
- a `VerificationReport` is what a reader keeps to show an answer was checked.
  Flip a `Claim` from unsupported to supported and the audit says the
  opposite of what happened;
- `slots` matters as much as `frozen`: without it a caller can hang an
  attribute off a value, and the next reader has no idea it is there.

So the rule is one rule, checked by walking. Four classes are exempt and each
says why; an exemption is a decision somebody made, not a hole.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil

import pytest

import tsumugi

#: Dataclasses that accumulate, with the reason. Each is built up by a loop and
#: rebuilds its own fields as it goes, so it is a *builder* rather than a value
#: — and freezing one would be a rewrite of the thing that fills it rather than
#: a guarantee about the thing it produces.
MUTABLE_ON_PURPOSE = {
    "Summary": "scoring rebuilds its tuples case by case as it walks the corpus",
    "AnswerSummary": "the same, over answers rather than packages",
}


def _dataclasses() -> list[type]:
    """Every dataclass defined in this package, once each."""
    found: dict[str, type] = {}
    for module in pkgutil.walk_packages(tsumugi.__path__, f"{tsumugi.__name__}."):
        imported = importlib.import_module(module.name)
        for _name, obj in inspect.getmembers(imported, inspect.isclass):
            if dataclasses.is_dataclass(obj) and obj.__module__.startswith("tsumugi."):
                found[f"{obj.__module__}.{obj.__name__}"] = obj
    return sorted(found.values(), key=lambda c: f"{c.__module__}.{c.__name__}")


def _identify(cls: type) -> str:
    return f"{cls.__module__.removeprefix('tsumugi.')}.{cls.__name__}"


class TestEveryValueIsFrozenAndSlotted:
    def test_the_walk_finds_a_realistic_number(self) -> None:
        """The positive control, and it has to come first.

        A walk that found nothing would make every assertion below pass over
        an empty list — which is exactly the shape of failure this file exists
        to catch elsewhere.
        """
        found = _dataclasses()
        assert len(found) > 30, len(found)
        names = {cls.__name__ for cls in found}
        assert {"Anchor", "ContextPackage", "VerificationReport", "Lead"} <= names, names

    @pytest.mark.parametrize("cls", _dataclasses(), ids=_identify)
    def test_it_is_frozen(self, cls: type) -> None:
        if cls.__name__ in MUTABLE_ON_PURPOSE:
            pytest.skip(MUTABLE_ON_PURPOSE[cls.__name__])
        assert cls.__dataclass_params__.frozen, (  # type: ignore[attr-defined]
            f"{_identify(cls)} can be edited after it is handed over"
        )

    @pytest.mark.parametrize("cls", _dataclasses(), ids=_identify)
    def test_it_has_slots(self, cls: type) -> None:
        """`slots=True`, checked as the class attribute rather than by trying
        an assignment: which exception that raises is a Python version's
        business (3.12 gives `TypeError`, 3.13 `FrozenInstanceError`) and
        pinning either turned an earlier test red on half the CI matrix."""
        assert "__slots__" in cls.__dict__, f"{_identify(cls)} has a dict to stash things in"


class TestTheExemptionsAreArguments:
    def test_every_exempt_name_is_a_real_dataclass(self) -> None:
        """An exemption for a class that no longer exists is a stale licence."""
        names = {cls.__name__ for cls in _dataclasses()}
        for name in MUTABLE_ON_PURPOSE:
            assert name in names, f"{name} is exempt and does not exist"

    def test_every_exemption_gives_a_reason(self) -> None:
        for name, reason in MUTABLE_ON_PURPOSE.items():
            assert len(reason.split()) >= 5, f"{name}'s exemption does not explain itself"

    def test_no_exempt_class_is_frozen_anyway(self) -> None:
        """The other direction: an exemption nobody needs any more.

        If one of these gets frozen, the licence should go with it rather than
        sit there permitting something that is no longer happening.
        """
        for cls in _dataclasses():
            if cls.__name__ in MUTABLE_ON_PURPOSE:
                assert not cls.__dataclass_params__.frozen, (  # type: ignore[attr-defined]
                    f"{cls.__name__} is frozen now; remove its exemption"
                )


class TestWhatFreezingActuallyPrevents:
    """One worked example each, so the rule above is not only arithmetic."""

    def test_a_claims_verdict_cannot_be_forged(self) -> None:
        """`support` is derived rather than stored, which is the better half of
        this design: nobody can set it at all.

        What freezing protects is what it is derived *from*. A caller who could
        rewrite `citations` or `unverifiable_because` would change the verdict
        without touching it, and the report would read as checked.
        """
        from tsumugi.domain.claim import Claim, Support

        claim = Claim(text="the tent weighs nine kilos")
        assert claim.support is Support.UNCITED
        assert not hasattr(Claim, "support") or isinstance(
            inspect.getattr_static(Claim, "support"), property
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            claim.citations = ()  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            claim.unverifiable_because = ""  # type: ignore[misc]

    def test_a_value_has_nowhere_to_hide_state(self) -> None:
        from tsumugi.domain.claim import Claim

        claim = Claim(text="a claim")
        assert not hasattr(claim, "__dict__")
