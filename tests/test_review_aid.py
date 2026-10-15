"""Compact tie-out review aid (evidence-review-I46): contract and browser behaviour."""

import pytest
from playwright.sync_api import expect, sync_playwright
from pydantic import ValidationError

from evidence_review import FileStore, open_review
from evidence_review.contracts import (
    AidComparison, AidComparisonRow, AidInput, AidQualification, AidStep, Citation, Claim, FormField,
    ReviewAid, validate_bundle,
)
from evidence_review.evidence import evaluate_interval, evidence_views
from synthetic import build, source, unavailable

SEGMENTS = (
    "# Synthetic segment results\n"
    "Prepared for testing only.\n"
    "\n"
    "| Segment | FY2025 sales | FY2026 sales |\n"
    "| --- | --- | --- |\n"
    "| X | 400 | 360 |\n"
    "| Y | 300 | 291 |\n"
    "| Z | 200 | 210 |\n"
    "\n"
    "Guidance: Z sales change of -4% to -2% next year.\n"
)
TEXT = "Segment X declines 10%; Y is milder at 3%; Z turns from growth to decline."


def seg(line, excerpt):
    return Citation(source_id="filing", start_line=line, end_line=line, excerpt=excerpt, status="located")


def segment_aid(z_guide=None):
    return ReviewAid(
        inputs=[
            AidInput(input_id="x_prior", label="X sales", value="400", unit="$m", period="FY2025", entity="X", citation=seg(6, "400")),
            AidInput(input_id="x_now", label="X sales", value="360", unit="$m", period="FY2026", entity="X", citation=seg(6, "360")),
            AidInput(input_id="y_prior", label="Y sales", value="300", unit="$m", period="FY2025", entity="Y", citation=seg(7, "300")),
            AidInput(input_id="y_now", label="Y sales", value="291", unit="$m", period="FY2026", entity="Y", citation=seg(7, "291")),
            z_guide or AidInput(input_id="z_guide", label="Z guided change", low="-4", high="-2", unit="%",
                                period="FY2027", entity="Z", approximate=True, citation=seg(10, "-4% to -2%")),
        ],
        steps=[
            AidStep(step_id="x_change", label="X change", formula="(x_now - x_prior) / x_prior * 100", unit="%"),
            AidStep(step_id="y_change", label="Y change", formula="(y_now - y_prior) / y_prior * 100", unit="%"),
            AidStep(step_id="z_next", label="Z next-year sales", formula="210 * (1 + z_guide / 100)", unit="$m"),
        ],
        comparison=AidComparison(columns=["FY2025", "FY2026", "Change"], rows=[
            AidComparisonRow(label="X", cells=["x_prior", "x_now", "x_change"]),
            AidComparisonRow(label="Y", cells=["y_prior", "y_now", "y_change"]),
        ]),
        qualifications=[
            AidQualification(kind="period_mismatch", text="Z compares a full-year actual with next-year guidance."),
            AidQualification(kind="human_judgment", text="Whether 3% is \"milder\" is a judgment."),
        ],
    )


def segment_bundle(aid=None):
    claim = Claim(claim_id="segments", text=TEXT, citations=[seg(6, "400")], review_aid=aid or segment_aid())
    form = [FormField(field_id="support:segments", label="Support: segments", subject_id="segments",
                      options=["supported", "unsupported", "cannot determine"])]
    mapping = {("answer", "10%"): ("cited", ["segments"]), ("answer", "3%"): ("cited", ["segments"])}
    return build([("answer", TEXT, ["segments"])], [claim], mapping, sources=[source(SEGMENTS)], form=form)


def test_signed_interval_arithmetic_keeps_full_ranges():
    assert evaluate_interval("-x", {"x": ("1", "2")}) == (-2, -1)
    assert evaluate_interval("x * y", {"x": ("-2", "3"), "y": ("-5", "4")}) == (-15, 12)
    lo, hi = evaluate_interval("x / y", {"x": ("24.6", "24.6"), "y": ("-3", "-1")})
    assert (str(lo), str(hi)) == ("-24.6", "-8.2")
    with pytest.raises(ZeroDivisionError):
        evaluate_interval("x / y", {"x": ("1", "1"), "y": ("-1", "1")})


def test_steps_are_derived_with_units_periods_and_signed_results():
    aid = segment_aid()
    steps = aid.computation["steps"]
    assert steps["x_change"] == {"status": "computed", "value": "-10.0"}
    assert steps["y_change"] == {"status": "computed", "value": "-3.00"}
    # A guided range stays a range; it is never collapsed to its midpoint.
    assert steps["z_next"] == {"status": "computed", "low": "201.60", "high": "205.80"}
    assert aid.computation["unresolved_inputs"] == []
    assert aid.computation["evidence_verified"] is False


def test_a_producer_cannot_supply_its_own_result():
    data = segment_aid().model_dump(mode="json")
    data["computation"] = {"steps": {"x_change": {"status": "computed", "value": "-99"}}}
    assert ReviewAid.model_validate(data).computation["steps"]["x_change"]["value"] == "-10.0"


def test_missing_evidence_leaves_dependent_steps_unresolved():
    missing = AidInput(input_id="z_guide", label="Z guided change", unit="%", citation=unavailable("guidance not in sources"))
    aid = segment_aid(missing)
    assert aid.computation["unresolved_inputs"] == ["z_guide"]
    assert aid.computation["steps"]["z_next"] == {"status": "unresolved", "reason": "unresolved input: z_guide"}
    assert aid.computation["steps"]["x_change"]["status"] == "computed"


def test_divisor_range_through_zero_is_a_calculation_error_not_a_number():
    aid = ReviewAid(inputs=[AidInput(input_id="a", label="A", value="5", citation=seg(6, "400")),
                            AidInput(input_id="b", label="B", low="-1", high="1", citation=seg(6, "400"))],
                    steps=[AidStep(step_id="q", label="Q", formula="a / b")])
    assert aid.computation["steps"]["q"]["status"] == "calculation_error"


@pytest.mark.parametrize("kwargs", [
    {"low": "1"},
    {"value": "1", "low": "1", "high": "2"},
    {"low": "3", "high": "2"},
    {"value": "nan"},
    {"value": ""},
])
def test_malformed_inputs_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        AidInput(input_id="a", label="A", citation=seg(6, "400"), **kwargs)


def test_steps_may_only_read_earlier_ids_and_safe_syntax():
    a = AidInput(input_id="a", label="A", value="1", citation=seg(6, "400"))
    with pytest.raises(ValidationError):
        ReviewAid(inputs=[a], steps=[AidStep(step_id="s", label="S", formula="a + later"),
                                    AidStep(step_id="later", label="L", formula="a")])
    with pytest.raises(ValidationError):
        ReviewAid(inputs=[a], steps=[AidStep(step_id="s", label="S", formula="__import__('os')")])
    with pytest.raises(ValidationError):
        ReviewAid(inputs=[a], comparison=AidComparison(columns=["p", "q"], rows=[AidComparisonRow(label="r", cells=["a", "nope"])]))
    with pytest.raises(ValidationError):
        ReviewAid(inputs=[a, a])


def test_bundle_validates_aid_citations_and_keeps_legacy_hashes():
    b = segment_bundle()
    assert validate_bundle(b.model_dump(mode="json")).bundle_hash == b.bundle_hash
    assert "filing:10:10" in evidence_views(b)
    plain = segment_bundle()
    plain.claims[0].review_aid = None
    assert "review_aid" not in plain.model_dump(mode="json")["claims"][0]
    bad = b.model_dump(mode="json")
    bad["claims"][0]["review_aid"]["inputs"][0]["citation"]["excerpt"] = "not in the source"
    with pytest.raises(ValidationError):
        validate_bundle(bad)


def test_tie_out_precedes_the_verdict_and_links_inputs_to_their_source(tmp_path):
    b = segment_bundle()
    with open_review(b, FileStore(tmp_path), launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        item = page.get_by_label("Current item")
        aid = item.get_by_role("region", name="Tie-out for this statement")
        expect(aid).to_be_visible()
        expect(aid.locator("[data-aid-step=x_change]")).to_contain_text("= -10.0 %")
        expect(aid.locator("[data-aid-step=z_next]")).to_contain_text("201.60 to 205.80 $m")
        expect(aid.locator("[data-aid-input=z_guide]")).to_contain_text("≈ -4 to -2 %")
        expect(aid.locator(".review-aid-comparison")).to_contain_text("-3.00 %")
        expect(aid.locator("[data-qualification=period_mismatch]")).to_contain_text("Period mismatch")
        expect(aid).to_contain_text("never a support judgment")
        # Input → arithmetic → qualification → verdict, in that order within one item.
        assert aid.evaluate("(n) => !!(n.compareDocumentPosition(n.closest('.item').querySelector('.item-decisions')) & Node.DOCUMENT_POSITION_FOLLOWING)")
        # Original excerpts are available but no longer the primary view.
        excerpts = item.locator("details.item-atoms")
        expect(excerpts).to_have_count(1)
        assert not excerpts.evaluate("(n) => n.open")
        aid.get_by_role("button", name="Open source for X sales").first.click()
        expect(page.locator("#viewer")).to_contain_text("400")
        # Viewing a tie-out never records a judgment.
        assert page.get_by_label("Support: segments", exact=True).input_value() in ("", "pending")
        browser.close()


def test_unresolved_input_is_visible_in_the_tie_out(tmp_path):
    missing = AidInput(input_id="z_guide", label="Z guided change", unit="%", citation=unavailable("guidance not in sources"))
    b = segment_bundle(segment_aid(missing))
    with open_review(b, FileStore(tmp_path), launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        aid = page.get_by_role("region", name="Tie-out for this statement")
        expect(aid.locator("[data-aid-input=z_guide]")).to_contain_text("Source unresolved: guidance not in sources")
        expect(aid.locator("[data-aid-step=z_next]")).to_contain_text("unresolved (unresolved input: z_guide)")
        browser.close()
