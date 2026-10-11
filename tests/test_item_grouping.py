from playwright.sync_api import expect, sync_playwright
from evidence_review import FileStore, open_review
from evidence_review.contracts import Claim, FormField
from synthetic import build, located


def grouped_bundle():
    claims = [Claim(claim_id="margin", text="Margin was 24.6%.", citations=[located(6, "24.6%")]),
              Claim(claim_id="revenue", text="Revenue was 1,150.", citations=[located(7, "1,150")])]
    form = [FormField(field_id="conclusion", label="Central conclusion in your words", kind="text")]
    for c in claims:
        form.append(FormField(field_id="support:" + c.claim_id, label="Support: " + c.claim_id,
                              subject_id=c.claim_id, options=["supported", "cannot determine"]))
        form.append(FormField(field_id="material:" + c.claim_id, label="Materiality: " + c.claim_id,
                              subject_id=c.claim_id, options=["material", "minor"], required=False))
    form.append(FormField(field_id="complete", label="Review complete", kind="boolean", require_true=True))
    b = build([(c.claim_id, c.text, [c.claim_id]) for c in claims], claims,
              {(c.claim_id, value): ("cited", [c.claim_id]) for c, value in zip(claims, ["24.6%", "1,150"])}, form=form)
    for s in b.spans:
        s.citations = next(c.citations for c in claims if c.claim_id in s.claim_ids)
    return type(b).model_validate(b.model_dump(mode="json"))


def test_atoms_and_related_controls_live_with_their_item(tmp_path):
    b = grouped_bundle()
    with open_review(b, FileStore(tmp_path), launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        item = page.get_by_label("Current item")
        expect(item.locator(".atom-guidance")).to_be_visible()
        expect(item.locator(".atom-guidance")).to_contain_text("no separate check is assigned")
        expect(item.locator("[data-atom-row]")).to_have_count(1)
        expect(item.locator("[data-atom-row]")).to_contain_text("24.6%")
        item.get_by_text("Materiality: margin", exact=True).click()
        expect(item.get_by_label("Materiality: margin", exact=True)).to_be_visible()
        expect(page.locator("#atoms")).to_be_hidden()
        item.get_by_role("button", name="Open the source", exact=False).click()
        expect(page.locator("#viewer")).to_contain_text("24.6%")
        page.get_by_role("region", name="Report").get_by_role("button", name="1,150: go to its check row", exact=False).click()
        expect(item).to_contain_text("Revenue was 1,150.")
        expect(item.locator("[data-atom-row]")).to_have_count(1)
        expect(item.locator("[data-atom-row]")).to_contain_text("1,150")
        expect(page.locator("[data-atom-row]")).to_have_count(2)
        browser.close()


def test_whole_answer_is_after_items_and_invalid_submit_focuses_it_without_losing_draft(tmp_path):
    b = grouped_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        expect(page.locator("#intro-forms").get_by_label("Central conclusion in your words", exact=True)).to_be_visible()
        assert page.locator("#item-navigation").evaluate("(n) => !!(n.compareDocumentPosition(document.querySelector(\"#intro\")) & Node.DOCUMENT_POSITION_FOLLOWING)")
        page.get_by_label("Support: margin", exact=True).select_option("supported")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#status")).to_contain_text("Central conclusion in your words")
        expect(page.get_by_label("Central conclusion in your words", exact=True)).to_be_focused()
        page.get_by_label("Central conclusion in your words", exact=True).fill("A synthetic conclusion.")
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.reload()
        expect(page.get_by_label("Central conclusion in your words", exact=True)).to_have_value("A synthetic conclusion.")
        assert store.load_task(b.bundle_id)["answers"]["judgments"]["support:margin"]["value"] == "supported"
        browser.close()


def test_hidden_optional_whole_answer_error_is_revealed_and_focused(tmp_path):
    b = grouped_bundle()
    b.form = [FormField(field_id="scope", label="Whole answer concern", options=["yes", "no"],
                        required=False, note_required_unless=["yes"]),
              FormField(field_id="complete", label="Review complete", kind="boolean", require_true=True)]
    b = type(b).model_validate(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    store.register(b)
    store.save_snapshot(b, 0, "draft", {"judgments": {"scope": {"value": "no"}, "complete": {"value": True}}},
                        "Synthetic reviewer", 0)
    with open_review(b, store, launch=False, required_fields_only=True) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        expect(page.get_by_label("Whole answer concern", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.get_by_label("Whole answer concern", exact=True)).to_be_focused()
        assert store.load_task(b.bundle_id)["last_submission"] is None
        browser.close()


def test_review_steps_collapse_without_changing_answers_and_reopen_for_navigation(tmp_path):
    b = grouped_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        intro = page.get_by_label("Central conclusion in your words", exact=True)
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        intro.fill("Saved conclusion.")
        expect(page.locator("#status")).to_contain_text("Saved locally")
        intro_step = page.locator("#intro-forms > details")
        intro_step.locator(":scope > summary").click()
        page.get_by_label("Support: margin", exact=True).select_option("supported")
        expect(intro).to_be_hidden()  # survives a form rerender
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.get_by_label("Support: margin", exact=True).select_option("")
        expect(page.locator("#status")).to_contain_text("Saved locally")
        before = store.load_task(b.bundle_id)["answers"]
        current = page.get_by_label("Current item", exact=True)
        expanded_height = current.bounding_box()["height"]
        current.locator(":scope > summary").focus()
        page.keyboard.press("Enter")
        expect(current.locator(".item-atoms")).to_be_hidden()
        assert current.bounding_box()["height"] < expanded_height / 2
        page.reload()
        expect(page.locator("#status")).to_contain_text("Saved locally")
        expect(intro).to_be_hidden()
        expect(current.locator(".item-atoms")).to_be_hidden()
        assert store.load_task(b.bundle_id)["answers"] == before
        page.get_by_role("region", name="Report").get_by_role("button", name="24.6%: go to its check row", exact=False).click()
        expect(current.locator(".item-atoms")).to_be_visible()
        intro_step.locator(":scope > summary").click()
        expect(intro).to_have_value("Saved conclusion.")
        intro.fill("")
        intro_step.locator(":scope > summary").click()
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(intro).to_be_visible()
        expect(intro).to_be_focused()
        browser.close()
