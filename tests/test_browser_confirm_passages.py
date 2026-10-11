"""Proposed frozen passages become judgment evidence only after reviewer confirmation."""

import pytest
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


def test_supported_confirms_all_statement_passages_in_one_saved_action(tmp_path):
    b = passage_bundle()
    b.form[0].evidence_required = False  # Consumer-owned conditional requirement, like existing bundles.
    b.claims[0].citations.append(located(3, "review rule"))
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    store.register(b)
    store.save_snapshot(b, 0, "old-answer", {"judgments": {"support:fees": {"value": "supported"}}}, "Synthetic reviewer", 0)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        item = page.get_by_label("Current item")
        passage = item.get_by_role("group", name="Proposed source passages")
        expect(passage.get_by_role("button", name="Confirm checked passage", exact=True)).to_have_count(0)
        assert not store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]["selections"]
        expect(item.get_by_label("Support: fees", exact=True).locator("option:checked")).to_have_text("Supported saved — confirm evidence")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#status")).to_contain_text("confirm the proposed evidence")
        assert store.load_task(b.bundle_id)["last_submission"] is None
        passage.get_by_role("button", name="View source passage", exact=True).first.click()
        assert not store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]["selections"]
        item.get_by_label("Support: fees", exact=True).select_option("supported")
        expect(page.locator("#status")).to_contain_text("revision 2")
        saved = store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]
        assert saved["value"] == "supported"
        assert [(s["start_line"], s["subject_id"]) for s in saved["selections"]] == [(2, "fees"), (3, "fees")]
        assert saved["selections"][0]["excerpt"] == "Refunds exclude service fees."
        page.reload()
        fees = page.locator('[data-item-subject="fees"]')
        expect(fees.get_by_label("Support: fees", exact=True)).to_have_value("supported")
        fees.get_by_label("Support: fees", exact=True).select_option("supported")
        assert len(store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]["selections"]) == 2
        item.get_by_role("button", name="Next unanswered item", exact=True).click()
        item.get_by_label("Support: review", exact=True).select_option("cannot determine")
        expect(page.locator("#status")).to_contain_text("revision 4")
        assert not store.load_task(b.bundle_id)["answers"]["judgments"]["support:review"]["selections"]
        item.get_by_label("Support: review", exact=True).select_option("supported")
        page.get_by_label("Full review complete", exact=True).check()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
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
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        item.get_by_label("Support: fees", exact=True).select_option("supported")
        expect(page.locator("#status")).to_contain_text("revision 1")
        saved = FileStore(tmp_path).load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]
        assert not saved["selections"]
        expect(page.get_by_label("Review progress")).to_contain_text("0 of 3 required fields complete")
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

def test_inference_proposes_named_premise_passages_without_changing_originals(tmp_path):
    b = passage_bundle()
    b.claims = [
        Claim(claim_id="C1", text="First premise", citations=[located(2, "exclude service fees")]),
        Claim(claim_id="C2", text="Second premise", citations=[located(3, "review rule")]),
        Claim(claim_id="C3", text="Inference", support_declaration="inference: Weighting C1-C2."),
    ]
    b.fields[0].claim_ids = ["C1", "C2", "C3"]
    b.form = [FormField(field_id="support:C3", label="Support inference", subject_id="C3",
                        options=["supported", "cannot determine"], evidence_required=True)]
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        proposals = page.get_by_role("group", name="Proposed source passages")
        expect(proposals).to_contain_text("C1, C2")
        expect(proposals.get_by_role("button", name="View source passage")).to_have_count(2)
        assert not store.load_task(b.bundle_id)["answers"]["judgments"]
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.get_by_label("Support inference", exact=True).select_option("supported")
        expect(page.locator("#status")).to_contain_text("revision 1")
        saved = store.load_task(b.bundle_id)["answers"]["judgments"]["support:C3"]
        assert [(s["start_line"], s["subject_id"]) for s in saved["selections"]] == [(2, "C3"), (3, "C3")]
        assert b.claims[2].citations == []
        browser.close()


def test_missing_proposals_name_actual_blocker_instead_of_reselection_loop(tmp_path):
    b = passage_bundle()
    b.claims[0].citations = []
    store = FileStore(tmp_path)
    store.register(b)
    store.save_snapshot(b, 0, "old-answer", {"judgments": {"support:fees": {"value": "supported"}}}, "Synthetic reviewer", 0)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#status")).to_contain_text("Support: fees")
        expect(page.locator("#status")).to_contain_text("no exact passage is proposed")
        browser.close()

def test_pending_supported_confirmation_works_with_native_keyboard(tmp_path):
    b = passage_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    store.save_snapshot(b, 0, "legacy", {"judgments": {"support:fees": {"value": "supported"}}}, "Synthetic reviewer", 0)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        control = page.get_by_label("Support: fees", exact=True)
        expect(control.locator("option:checked")).to_have_text("Supported saved — confirm evidence")
        assert store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]["value"] == "supported"
        group = page.locator('details.item[data-item-subject="fees"]')
        if group.get_attribute("open") is None:
            group.locator(":scope > summary").click()
        expect(control).to_be_visible()
        control.focus()
        page.keyboard.press("s")
        page.keyboard.press("Tab")
        expect(page.locator("#status")).to_contain_text("revision 2")
        saved = store.load_task(b.bundle_id)["answers"]["judgments"]["support:fees"]
        assert saved["value"] == "supported" and len(saved["selections"]) == 1
        browser.close()


def test_native_recomputation_selection_confirms_linked_worksheet_and_passages(tmp_path):
    from test_reviewer_worksheet import worksheet_bundle

    b = worksheet_bundle()
    span = next(s for s in b.spans if s.text == "180 bps")
    span.calculation.formula = "(current - prior) * 100"
    field = FormField(field_id="calc:" + span.span_id, label="Recomputed margin",
                      options=["recomputed_matches", "recomputed_differs", "cannot determine"], subject_id="margin")
    b.form.insert(0, field)
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    store.register(b)
    store.save_snapshot(b, 0, "old-calculation", {"judgments": {field.field_id: {"value": "recomputed_matches"}}}, "Synthetic reviewer", 0)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        group = page.locator('details.item[data-item-subject="margin"]')
        if group.get_attribute("open") is None:
            group.locator(":scope > summary").click()
        control = page.get_by_label(field.label, exact=True)
        expect(control.locator("option:checked")).to_have_text("Recomputation saved — confirm worksheet")
        preview = page.get_by_role("group", name="Proposed calculation worksheet")
        expect(group.get_by_role("button", name="Confirm checked passage", exact=True)).to_have_count(0)
        expect(group.get_by_role("button", name="Attach selected source passage", exact=True)).to_have_count(0)
        expect(preview).to_contain_text("(current - prior) * 100")
        expect(preview).to_contain_text("24.6%")
        assert not store.load_task(b.bundle_id)["answers"].get("worksheets")
        expect(page.get_by_label("Review progress")).to_contain_text("0 of 3 required fields complete")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#status")).to_contain_text("confirm the proposed worksheet")
        assert store.load_task(b.bundle_id)["last_submission"] is None
        control.focus()
        page.keyboard.press("r")
        page.keyboard.press("Tab")
        expect(page.get_by_label("Explanation: Recomputed margin", exact=True)).to_be_focused()
        expect(page.locator("#status")).to_contain_text("revision 2")
        saved = store.load_task(b.bundle_id)["answers"]
        assert saved["judgments"][field.field_id]["value"] == "recomputed_matches"
        w = saved["worksheets"][0]
        assert w["subject"] == {"kind": "span", "id": span.span_id}
        assert w["computation"]["comparison"] == "match"
        assert w["reviewer_support"] == "unknown"
        assert all(o["selections"][0]["excerpt"] for o in w["operands"])
        assert "Confirmed supplied formula" in w["rationale"]
        page.reload()
        expect(page.get_by_label(field.label, exact=True)).to_have_value("recomputed_matches")
        assert len(store.load_task(b.bundle_id)["answers"]["worksheets"]) == 1
        assert page.locator("#worksheet-tools").get_attribute("open") is None
        group.get_by_role("button", name="Use supplied calculation as draft worksheet", exact=True).click()
        expect(page.get_by_role("group", name="Worksheet 1", exact=True)).to_be_visible()
        page.get_by_role("button", name="Remove worksheet 1", exact=True).click()
        expect(page.locator("#status")).to_contain_text("revision 3")
        expect(page.get_by_label(field.label, exact=True).locator("option:checked")).to_have_text(
            "Recomputation saved — confirm worksheet")
        browser.close()


def test_recomputation_selection_preserves_edited_worksheet(tmp_path):
    from test_reviewer_worksheet import worksheet_bundle, revenue_worksheet

    b = worksheet_bundle()
    b.form.insert(0, FormField(field_id="calc:margin", label="Margin arithmetic",
                              options=["recomputed_matches", "recomputed_differs"], subject_id="margin"))
    edited = revenue_worksheet(b, subject={"kind": "claim", "id": "margin"})
    store = FileStore(tmp_path)
    store.register(b)
    store.save_snapshot(b, 0, "edited", {"worksheets": [edited]}, "Synthetic reviewer", 0)
    original = store.load_task(b.bundle_id)["answers"]["worksheets"]
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        control = page.get_by_label("Margin arithmetic", exact=True)
        control.select_option("recomputed_differs")
        expect(page.locator("#status")).to_contain_text("revision 2")
        assert store.load_task(b.bundle_id)["answers"]["worksheets"] == original
        control.select_option("recomputed_matches")
        expect(page.locator("#status")).to_contain_text("revision 3")
        assert store.load_task(b.bundle_id)["answers"]["worksheets"] == original
        browser.close()


def test_unsourced_calculation_does_not_invent_a_reviewer_worksheet(tmp_path):
    from test_reviewer_worksheet import worksheet_bundle

    b = worksheet_bundle()
    b.claims[0].calculation.operands[0].citation = None
    b.form.insert(0, FormField(field_id="calc:margin", label="Margin arithmetic",
                              options=["recomputed_matches", "recomputed_differs"], subject_id="margin"))
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        preview = page.get_by_role("group", name="Proposed calculation worksheet")
        expect(preview).to_contain_text("No exact source passage supplied")
        page.get_by_label("Margin arithmetic", exact=True).select_option("recomputed_matches")
        expect(page.locator("#status")).to_contain_text("revision 1")
        assert not store.load_task(b.bundle_id)["answers"].get("worksheets")
        page.reload()
        expect(page.get_by_label("Margin arithmetic", exact=True).locator("option:checked")).to_have_text(
            "Recomputation saved — worksheet required")
        browser.close()


def test_evidence_required_calculation_keeps_its_judgment_attachment_path(tmp_path):
    from test_reviewer_worksheet import worksheet_bundle

    b = worksheet_bundle()
    b.form = [FormField(field_id="calc:margin", label="Margin arithmetic", evidence_required=True,
                        options=["recomputed_differs"], subject_id="margin")]
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.get_by_label("Margin arithmetic", exact=True).select_option("recomputed_differs")
        expect(page.locator("#status")).to_contain_text("revision 1")
        expect(page.get_by_role("button", name="Attach selected source passage", exact=True)).to_be_visible()
        page.get_by_role("button", name="Confirm checked passage", exact=True).first.click()
        expect(page.locator("#status")).to_contain_text("revision 2")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
        browser.close()

def test_correction_after_failed_submission_needs_no_duplicate_tools_or_reason(tmp_path):
    b = passage_bundle()
    for field in b.form:
        field.evidence_required = False
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    store.register(b)
    answers = {"judgments": {"support:fees": {"value": "cannot determine"},
                            "support:review": {"value": "cannot determine"},
                            "complete": {"value": True}}, "defects": []}
    store.save_submission(b, 0, "first", answers, "Ada", 0)
    store.hook_status(b.bundle_id, 1, {"status": "failed", "reason": "consumer rejected", "identifier": None})
    answers["judgments"]["support:fees"]["note"] = "Corrected after checking."
    store.save_snapshot(b, 1, "draft", answers, "Ada", 0)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 2")
        assert page.locator("#annotation-tools").get_attribute("open") is None
        assert page.locator("#worksheet-tools").get_attribute("open") is None
        expect(page.locator("#amendment")).not_to_be_visible()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
        submission = store.export_submission(b.bundle_id, 3)
        assert submission.amendment_reason == "Correction after failed continuation of revision 1."
        assert not submission.annotations and not submission.worksheets
        browser.close()


@pytest.mark.parametrize("plain_label, table_label, matches", [("Revenue", "Revenue", True), ("Class_A", "Class A", False)])
def test_flattened_citation_shows_unique_matching_frozen_table(tmp_path, plain_label, table_label, matches):
    from evidence_review.source_rendering import prepare_render_asset
    text = "# Results\nRevenue $ 120$ 100 20 %\n\n| Metric | Current | Prior | Change |\n| --- | --- | --- | --- |\n| Revenue | $ 120 | $ 100 | 20 % |\n"
    text = text.replace("Revenue $", plain_label + " $").replace("| Revenue |", "| " + table_label + " |")
    claim = Claim(claim_id="revenue", text="Revenue was $120.", citations=[located(2, "$ 120")])
    b = build([("answer", claim.text, ["revenue"])], [claim], sources=[source(text)],
              form=[FormField(field_id="support:revenue", label="Revenue support", subject_id="revenue",
                              options=["supported", "cannot determine"])], bundle_id="flattened-table")
    asset = prepare_render_asset(b, b.sources[0].source_id)
    with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset],
                     presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        page.get_by_role("button", name="View source passage", exact=True).first.click()
        table = page.get_by_label("Matching source table", exact=True)
        if matches:
            expect(table).to_be_visible()
            expect(table.locator("tr.target-hit")).to_contain_text("$ 120")
            expect(table.locator("th").first).to_have_text("Metric")
            expect(page.get_by_text("Original cited passage and full source", exact=True)).to_be_visible()
        else:
            expect(table).to_have_count(0)
            expect(page.locator("#viewer .target-hit")).to_contain_text("Class_A")
        browser.close()
