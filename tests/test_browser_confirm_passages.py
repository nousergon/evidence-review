"""Proposed frozen passages become judgment evidence only after reviewer confirmation."""

from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.contracts import Claim, FormField
from synthetic import build, located, source, unavailable


def passage_bundle():
    text = "# Synthetic policy\nRefunds exclude service fees.\nA separate review rule applies.\n"
    claims = [
        Claim(claim_id="fees", text="Service fees cannot be refunded.", citations=[located(2, "exclude service fees")]),
        Claim(claim_id="review", text="The review rule is separate.", citations=[located(3, "review rule")]),
    ]
    form = [FormField(field_id="support:" + c.claim_id, label="Support: " + c.claim_id,
                      options=["supported", "cannot determine"], subject_id=c.claim_id,
                      evidence_required=True) for c in claims]
    form.append(FormField(field_id="complete", label="Full review complete", kind="boolean", require_true=True))
    return build([("answer", "Service fees cannot be refunded. The review rule is separate.", ["fees", "review"])],
                 claims, sources=[source(text)], form=form, bundle_id="confirm-passages")


def test_proposed_passage_is_explicit_correct_subject_durable_and_removable(tmp_path):
    """Wrong subject, automatic attachment/verdict, mutated excerpts and lost receipts all fail here."""
    b = passage_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        item = page.get_by_label("Current item")
        passage = item.get_by_role("group", name="Proposed source passages")
        expect(passage).to_contain_text("Synthetic filing")
        expect(passage).to_contain_text("L2–L2")
        expect(passage).to_contain_text("Refunds exclude service fees.")
        assert not store.load_task(b.bundle_id)["answers"]["judgments"]
        passage.get_by_role("button", name="View source passage", exact=True).click()
        expect(page.locator("#viewer .target-hit")).to_contain_text("Refunds exclude service fees.")
        assert not store.load_task(b.bundle_id)["answers"]["judgments"]
        passage.get_by_role("button", name="Confirm checked passage", exact=True).click()
        expect(item.get_by_label("Support: fees", exact=True)).to_have_value("")
        expect(page.locator("#status")).to_contain_text("revision 1")
        expect(page.locator("#status")).to_contain_text("0/3 fields answered")
        expected = {"source_id": "filing", "source_hash": b.sources[0].sha256, "start_line": 2, "end_line": 2,
                    "excerpt": "Refunds exclude service fees.", "subject_id": "fees"}
        assert store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]["selections"] == [expected]
        expect(passage.get_by_role("button", name="Passage confirmed", exact=True)).to_be_disabled()
        item.get_by_label("Support: fees", exact=True).select_option("supported")
        expect(page.locator("#status")).to_contain_text("revision 2")
        page.reload()
        page.get_by_role("button", name="1. Support: fees · answered", exact=False).click()
        expect(item.get_by_label("Support: fees", exact=True)).to_have_value("supported")
        expect(item.get_by_role("button", name="Passage confirmed", exact=True)).to_be_disabled()
        item.get_by_role("button", name="Remove passage", exact=True).click()
        expect(item.get_by_role("button", name="Confirm checked passage", exact=True)).to_be_enabled()
        expect(page.locator("#status")).to_contain_text("revision 3")
        item.get_by_role("button", name="Confirm checked passage", exact=True).click()
        expect(page.locator("#status")).to_contain_text("revision 4")
        assert store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]["selections"] == [expected]
        item.get_by_role("button", name="Next unanswered item", exact=True).click()
        expect(item).to_contain_text("The review rule is separate.")
        item.get_by_role("button", name="Confirm checked passage", exact=True).click()
        item.get_by_label("Support: review", exact=True).select_option("supported")
        page.get_by_label("Full review complete", exact=True).check()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
        saved = store.load_task(b.bundle_id)["answers"]["judgments"]
        assert saved["support:review"]["selections"][0]["subject_id"] == "review"
        assert saved["support:review"]["selections"][0]["excerpt"] == "A separate review rule applies."
        assert saved["support:fees"]["selections"] == [expected]
        browser.close()


def test_missing_citations_offer_manual_review_without_a_confirmable_guess(tmp_path):
    b = passage_bundle()
    b.claims[0].citations = [unavailable("No exact citation was supplied")]
    b = type(b).model_validate(b.model_dump(mode="json"))
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        item = page.get_by_label("Current item")
        expect(item.get_by_role("group", name="Proposed source passages")).to_contain_text("No exact citation was supplied")
        expect(item.get_by_role("button", name="Confirm checked passage", exact=True)).to_have_count(0)
        expect(item.get_by_role("button", name="Attach selected source passage", exact=True)).to_be_visible()
        browser.close()

def test_current_calculation_prepares_one_linked_draft_with_named_constants(tmp_path):
    from evidence_review.contracts import Operand
    from test_reviewer_worksheet import worksheet_bundle

    b = worksheet_bundle()
    span = next(s for s in b.spans if s.text == "180 bps")
    span.calculation = span.calculation.model_copy(deep=True)
    span.calculation.formula = "(current - prior) * scale"
    span.calculation.operands.append(Operand(name="scale", value="100", unit="", kind="constant"))
    b.form.insert(0, FormField(field_id="calc:" + span.span_id, label="Recomputed margin",
                              options=["recomputed_matches", "cannot determine"], subject_id="margin"))
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        item = page.get_by_label("Current item")
        prepare = item.get_by_role("button", name="Use supplied calculation as draft worksheet", exact=True)
        prepare.click()
        expect(page.get_by_label("Worksheet 1 formula", exact=True)).to_have_value("(current - prior) * (100)")
        expect(page.locator("#status")).to_contain_text("revision 1")
        draft = store.load_task(b.bundle_id)["answers"]["worksheets"][0]
        assert draft["subject"] == {"kind": "span", "id": span.span_id}
        assert draft["reviewer_support"] == "unknown"
        assert [o["name"] for o in draft["operands"]] == ["current", "prior"]
        assert not any(o["selections"] for o in draft["operands"])
        assert not store.load_task(b.bundle_id)["answers"]["judgments"]
        prepare.click()
        expect(page.locator("#worksheets .worksheet")).to_have_count(1)
        assert span.calculation.formula == "(current - prior) * scale"
        browser.close()
