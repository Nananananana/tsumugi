"""Resolving a model's citations, and the four ways that can come out.

The failure this whole file guards against: a verifier that reports an honest
citation as fabricated. It is worse than no verification, because it teaches
its user to ignore the signal, and the signal is the product.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tsumugi.application.verify import (
    AnswerFormatError,
    ProtectedPackageError,
    parse_answer,
    verify_answer,
)
from tsumugi.domain.anchor import Anchor, ResolutionStatus, resolve
from tsumugi.domain.budget import Budget
from tsumugi.domain.claim import (
    Citation,
    Claim,
    Located,
    Support,
    VerificationReport,
    verify_claims,
)
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.matching import find_all, search_form
from tsumugi.domain.package import (
    BudgetReport,
    ContextPackage,
    PackageProvenance,
    Protection,
)
from tsumugi.domain.selection import ContextItem
from tsumugi.domain.span import Span

from .helpers import build_document

TEXT = (
    "# 予算\n\n予算の単位は呼び出し側で明示する。The unit is explicit at the call site.\n\n"
    "# 信頼境界\n\nそれはどのマシンかではなく、境界のどちら側かである。\n"
)
DOCUMENT = build_document("notes/design.md", TEXT)


def items() -> tuple[ContextItem, ...]:
    return (
        ContextItem(
            item_id="itm_001",
            text=TEXT[8:60],
            anchor=Anchor.into(DOCUMENT, Span(8, 60)),
            source_path=DOCUMENT.source_path,
            section="予算",
            cost=10,
        ),
        ContextItem(
            item_id="itm_002",
            text=TEXT[70:],
            anchor=Anchor.into(DOCUMENT, Span(70, len(TEXT))),
            source_path=DOCUMENT.source_path,
            section="信頼境界",
            cost=10,
        ),
    )


def a_package(**overrides: object) -> ContextPackage:
    selected = overrides.pop("items", items())
    defaults: dict[str, object] = {
        "query": "予算の単位は",
        "items": selected,
        "omissions": (),
        "budget": BudgetReport(
            Budget.characters(1000),
            sum(i.cost for i in selected),  # type: ignore[union-attr]
            "characters@1",
        ),
        "provenance": PackageProvenance(tsumugi_version="0.1.0.dev0"),
    }
    defaults.update(overrides)
    return ContextPackage(**defaults)  # type: ignore[arg-type]


class TestTheSearchForm:
    def test_whitespace_runs_collapse(self) -> None:
        assert search_form("a   \n\t b").text == "a b"

    def test_it_folds_case_and_width(self) -> None:
        assert search_form("Ｂｕｄｇｅｔ").text == search_form("BUDGET").text

    def test_leading_and_trailing_whitespace_is_dropped(self) -> None:
        # A model that quotes with a trailing newline has not made a mistake
        # worth reporting.
        assert search_form("  budget \n").text == "budget"

    def test_the_offset_map_points_back_into_the_original(self) -> None:
        form = search_form("  Hello   World  ")
        span = form.to_original(Span(0, 11))
        assert span.slice("  Hello   World  ") == "Hello   World"


class TestFinding:
    def test_an_exact_quotation_is_found(self) -> None:
        assert find_all("呼び出し側", TEXT)

    def test_a_quotation_that_is_not_there_is_not_nearly_there(self) -> None:
        # No fuzzy matching, no edit distance. Every step past the stated
        # tolerance trades a false negative for a false positive, and only one
        # of those is safe here.
        assert find_all("呼び出し先", TEXT) == []

    def test_whitespace_differences_do_not_break_a_match(self) -> None:
        assert find_all("The unit  is\n  explicit", TEXT)

    def test_case_and_width_differences_do_not_break_a_match(self) -> None:
        assert find_all("THE UNIT IS EXPLICIT", TEXT)

    def test_the_span_returned_covers_the_real_characters(self) -> None:
        span = find_all("呼び出し側", TEXT)[0]
        assert span.slice(TEXT) == "呼び出し側"

    def test_every_occurrence_is_returned(self) -> None:
        # Ambiguity is information, not an error.
        assert len(find_all("ab", "ab cd ab ef ab")) == 3

    def test_an_empty_quotation_finds_nothing(self) -> None:
        assert find_all("", TEXT) == []
        assert find_all("   ", TEXT) == []


class TestTheFourOutcomes:
    def test_a_real_quotation_is_supported(self) -> None:
        report = verify_claims([("the unit is stated", ["呼び出し側で明示する"])], items())
        assert report.claims[0].support is Support.SUPPORTED

    def test_an_invented_quotation_is_unsupported(self) -> None:
        report = verify_claims([("a fabrication", ["この文はどこにもない"])], items())
        assert report.claims[0].support is Support.UNSUPPORTED
        assert report.claims[0].unresolved

    def test_no_citation_at_all_is_uncited_not_unsupported(self) -> None:
        # A model that cites nothing has failed differently from one that
        # cites something that does not exist.
        report = verify_claims([("a bare assertion", [])], items())
        assert report.claims[0].support is Support.UNCITED

    def test_one_bad_citation_makes_the_whole_claim_unsupported(self) -> None:
        report = verify_claims(
            [("mixed", ["呼び出し側で明示する", "この文はどこにもない"])], items()
        )
        assert report.claims[0].support is Support.UNSUPPORTED

    def test_an_irreversibly_redacted_package_is_unverifiable(self) -> None:
        # Unknown and false are different, and a verifier that conflates them
        # teaches its user to ignore the signal (ADR-0009).
        report = verify_claims(
            [("something", ["呼び出し側で明示する"])],
            items(),
            unverifiable_because="masked beyond recovery",
        )
        assert report.claims[0].support is Support.UNVERIFIABLE

    def test_the_report_counts_every_state(self) -> None:
        report = verify_claims(
            [
                ("good", ["呼び出し側で明示する"]),
                ("bad", ["どこにもない"]),
                ("bare", []),
            ],
            items(),
        )
        assert report.counts == {
            "supported": 1,
            "unsupported": 1,
            "uncited": 1,
            "unverifiable": 0,
        }
        assert not report.clean


class TestWhatALocationIs:
    def test_a_resolved_citation_anchors_back_into_the_document(self) -> None:
        # The property that makes verification worth having: a citation
        # becomes an anchor that `trace` can follow to a line in a file.
        report = verify_claims([("x", ["呼び出し側で明示する"])], items())
        location = report.claims[0].citations[0].locations[0]

        assert resolve(location.anchor, DOCUMENT).status is ResolutionStatus.RESOLVED
        assert location.anchor.span.slice(DOCUMENT.content) == "呼び出し側で明示する"

    def test_it_names_the_item_and_the_section(self) -> None:
        report = verify_claims([("x", ["境界のどちら側か"])], items())
        location = report.claims[0].citations[0].locations[0]
        assert location.item_id == "itm_002"
        assert location.section == "信頼境界"

    def test_an_ambiguous_quotation_reports_every_place(self) -> None:
        short = ContextItem(
            item_id="itm_001",
            text="ab cd ab",
            anchor=Anchor.into(build_document("a.md", "ab cd ab"), Span(0, 8)),
            cost=1,
        )
        report = verify_claims([("x", ["ab"])], (short,))
        citation = report.claims[0].citations[0]
        assert citation.ambiguous
        assert len(citation.locations) == 2
        # Still supported: it is really there, twice.
        assert report.claims[0].support is Support.SUPPORTED


class TestParsingAnAnswer:
    def test_it_reads_the_schema_shape(self) -> None:
        payload = json.dumps({"claims": [{"text": "a", "citations": ["b"]}]})
        assert parse_answer(payload) == [("a", ["b"])]

    def test_a_bare_list_of_strings_becomes_uncited_claims(self) -> None:
        # What a model does when it ignores the schema. Saying "every claim is
        # uncited" is more useful than refusing to parse.
        assert parse_answer(json.dumps(["one", "two"])) == [("one", []), ("two", [])]

    def test_a_claim_with_no_citations_key_is_fine(self) -> None:
        assert parse_answer(json.dumps({"claims": [{"text": "a"}]})) == [("a", [])]

    def test_non_json_says_what_was_expected(self) -> None:
        with pytest.raises(AnswerFormatError, match="not JSON"):
            parse_answer("The answer is 42.")

    def test_a_missing_claims_key_is_named(self) -> None:
        with pytest.raises(AnswerFormatError, match="no 'claims' key"):
            parse_answer(json.dumps({"answer": "x"}))

    def test_a_claim_without_text_is_named_by_position(self) -> None:
        with pytest.raises(AnswerFormatError, match="claim 2"):
            parse_answer(json.dumps({"claims": [{"text": "a"}, {"citations": []}]}))

    def test_citations_that_are_not_strings_are_refused(self) -> None:
        with pytest.raises(AnswerFormatError, match="not strings"):
            parse_answer(json.dumps({"claims": [{"text": "a", "citations": [{"q": 1}]}]}))


class TestRestoreBeforeVerify:
    """ADR-0009, the constraint that is invisible from inside either library."""

    class _Redactor:
        name = "fake-redactor@1"
        scope = "sess_test"

        def protect(self, text: str) -> str:
            return text.replace("呼び出し側", "<PERSON_001>")

        def restore(self, text: str) -> str:
            return text.replace("<PERSON_001>", "呼び出し側")

    def test_an_unprotected_package_verifies_without_a_restorer(self) -> None:
        report = verify_answer([("x", ["呼び出し側で明示する"])], a_package(), redactor=None)
        assert report.claims[0].support is Support.SUPPORTED

    def test_a_protected_package_with_no_restorer_refuses_loudly(self) -> None:
        # The quiet version of this failure is the damaging one: every honest
        # citation would report as unsupported and look like a hallucination.
        protected = a_package(
            provenance=PackageProvenance(
                tsumugi_version="0.1.0.dev0",
                protection=Protection(by="mamori@0.12.0", scope="sess_2f11", reversible=True),
            )
        )
        with pytest.raises(ProtectedPackageError, match="sess_2f11"):
            verify_answer([("x", ["<PERSON_001>で明示する"])], protected)

    def test_restoring_first_makes_the_honest_citation_supported(self) -> None:
        protected = a_package(
            provenance=PackageProvenance(
                tsumugi_version="0.1.0.dev0",
                protection=Protection(by="fake-redactor@1", scope="sess_test", reversible=True),
            )
        )
        report = verify_answer(
            [("x", ["<PERSON_001>で明示する"])], protected, redactor=self._Redactor()
        )
        assert report.claims[0].support is Support.SUPPORTED

    def test_protection_does_not_change_any_classification(self) -> None:
        # The property worth having: privacy protection must not change what is
        # supported. Same answer, same verdicts, with and without the redactor.
        answer = [
            ("good", ["呼び出し側で明示する"]),
            ("bad", ["どこにもない"]),
            ("bare", []),
        ]
        plain = verify_answer(answer, a_package())

        redactor = self._Redactor()
        protected = a_package(
            provenance=PackageProvenance(
                tsumugi_version="0.1.0.dev0",
                protection=Protection(by="fake-redactor@1", scope="sess_test", reversible=True),
            )
        )
        through = verify_answer(
            [(t, [redactor.protect(q) for q in qs]) for t, qs in answer],
            protected,
            redactor=redactor,
        )

        assert [c.support for c in plain.claims] == [c.support for c in through.claims]

    def test_an_irreversible_protection_needs_no_restorer_and_says_why(self) -> None:
        blocked = a_package(
            provenance=PackageProvenance(
                tsumugi_version="0.1.0.dev0",
                protection=Protection(by="mamori@0.12.0", scope="s", reversible=False),
            )
        )
        report = verify_answer([("x", ["anything"])], blocked)
        assert report.claims[0].support is Support.UNVERIFIABLE
        assert "irreversibly" in report.claims[0].unverifiable_because


class TestReadingAPackageBack:
    def test_a_package_round_trips_through_json(self) -> None:
        original = a_package()
        assert ContextPackage.from_json(original.to_json()).package_id == original.package_id

    def test_an_altered_package_is_refused(self) -> None:
        # The id is checked, not trusted. One that came along for the ride
        # would look like a guarantee.
        payload = json.loads(a_package().to_json())
        payload["query"] = "a different question entirely"
        with pytest.raises(ValueError, match="has been altered"):
            ContextPackage.from_json(json.dumps(payload))

    def test_an_unrecognised_contract_is_refused(self) -> None:
        payload = json.loads(a_package().to_json())
        payload["contract"] = "tsumugi.context-package/9"
        with pytest.raises(ValueError, match="unrecognised contract"):
            ContextPackage.from_json(json.dumps(payload))

    def test_verification_works_against_a_package_read_from_disk(self) -> None:
        restored = ContextPackage.from_json(a_package().to_json())
        report = verify_answer([("x", ["呼び出し側で明示する"])], restored)
        assert report.claims[0].support is Support.SUPPORTED


quotations = st.text(alphabet=st.sampled_from(list("予算単位明示 abcABC\n。")), max_size=30)


class TestProperties:
    @given(quotation=quotations)
    def test_a_quotation_taken_from_the_text_always_resolves(self, quotation: str) -> None:
        # Constructed the other way round: any real substring of an item must
        # be findable, or the verifier would call a true citation false.
        if not quotation.strip():
            return
        item = items()[0]
        if search_form(quotation).text not in search_form(item.text).text:
            return
        report = verify_claims([("x", [quotation])], (item,))
        assert report.claims[0].support is Support.SUPPORTED

    @given(quotation=quotations)
    def test_verification_never_raises_on_arbitrary_text(self, quotation: str) -> None:
        verify_claims([("x", [quotation])], items())

    @given(quotation=quotations)
    def test_every_reported_location_really_contains_the_quotation(self, quotation: str) -> None:
        # The invariant that makes a "supported" verdict mean anything.
        report = verify_claims([("x", [quotation])], items())
        for citation in report.claims[0].citations:
            for location in citation.locations:
                found = location.anchor.span.slice(DOCUMENT.content)
                assert search_form(found).text == search_form(quotation).text


class TestAnAnswerThatAssertsNothing:
    """`all()` over nothing is true, and that was a fail-open.

    A model told to answer in JSON and unable to answer the question produces
    `{"claims": []}` -- which used to verify clean, exit 0 from `tsumugi
    verify`, and report as trustworthy from `ask`. The one place this library
    promises to fail closed is exactly this one.
    """

    def test_an_empty_report_is_not_clean(self) -> None:
        assert not VerificationReport.of([]).clean

    def test_it_is_distinguishable_from_a_failure(self) -> None:
        # Nothing checked and something failed are different, and neither is
        # success. A caller that cannot tell them apart will retry the wrong
        # one.
        empty = VerificationReport.of([])
        assert empty.asserts_nothing

        failed = VerificationReport.of([Claim(text="a claim", citations=(Citation("nope"),))])
        assert not failed.clean
        assert not failed.asserts_nothing

    def test_a_single_supported_claim_is_clean(self) -> None:
        # The other half still holds: this is not a stricter check, it is a
        # check that stops being vacuous.
        report = VerificationReport.of(
            [
                Claim(
                    text="a claim",
                    citations=(
                        Citation(
                            "quoted",
                            locations=(
                                Located(
                                    item_id="c1",
                                    anchor=Anchor(
                                        document_id="doc_1",
                                        span=Span(0, 6),
                                        text_hash=ContentHash.of("quoted"),
                                        version=ContentHash.of("quoted"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                )
            ]
        )
        assert report.clean and not report.asserts_nothing


class TestOneToleranceAndItsEdge:
    """A markdown fence is a syntax; prose around it is prose.

    Models wrap JSON in a fence constantly, including when told not to.
    Unwrapping one is reading a syntax rather than guessing at an intent --
    the same class of tolerance as NFKC in `domain.matching`, and it stops in
    the same place.
    """

    ANSWER = '{"claims": [{"text": "a claim", "citations": ["quoted"]}]}'

    def test_a_fenced_answer_is_read(self) -> None:
        fenced = "```json\n" + self.ANSWER + "\n```"
        assert parse_answer(fenced) == [("a claim", ["quoted"])]

    def test_a_fence_with_no_language_tag_is_read(self) -> None:
        assert parse_answer("```\n" + self.ANSWER + "\n```") == [("a claim", ["quoted"])]

    def test_a_fence_buried_in_prose_is_prose(self) -> None:
        # The line this refuses to cross. Hunting for JSON inside a page of
        # text is "find the answer somewhere in there", which is guessing --
        # and an answer not in the requested shape is a result, not a puzzle.
        with pytest.raises(AnswerFormatError):
            parse_answer("Here you go:\n```json\n" + self.ANSWER + "\n```\nHope that helps.")

    def test_unfenced_json_is_still_read(self) -> None:
        assert parse_answer(self.ANSWER) == [("a claim", ["quoted"])]

    def test_prose_is_still_refused(self) -> None:
        with pytest.raises(AnswerFormatError):
            parse_answer("The tent weighs 2.4kg, according to the notes.")
