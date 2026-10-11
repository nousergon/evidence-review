"""Supplied operand passages are proposed, then explicitly confirmed by the reviewer."""
from playwright.sync_api import expect, sync_playwright
from evidence_review import FileStore, open_review
from evidence_review.evidence import passage
from test_reviewer_worksheet import worksheet_bundle


def test_operand_proposals_need_confirmation_and_survive_reload(tmp_path):
    bundle = worksheet_bundle()
    store = FileStore(tmp_path)
    with open_review(bundle, store, launch=False) as handle, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(handle.url)
        page.get_by_label('Assessor', exact=True).fill('Synthetic reviewer')
        page.get_by_role('region', name='Report', exact=True).get_by_role('button', name='180 bps, derived', exact=True).click()
        page.get_by_role('button', name='Recompute in a reviewer worksheet', exact=True).click()
        rows = page.locator('#worksheets .worksheet-operand')
        expect(rows).to_have_count(2)
        for index in range(2):
            expect(rows.nth(index)).to_contain_text('Proposed source passages')
        expect(page.locator('#status')).not_to_contain_text('Saving')
        draft = store.load_task(bundle.bundle_id)['answers']['worksheets'][0]
        assert all(not operand['selections'] for operand in draft['operands'])
        for index in range(2):
            rows.nth(index).get_by_role('button', name='Confirm checked passage', exact=True).click()
        expect(page.locator('#status')).not_to_contain_text('Saving')
        kept = store.load_task(bundle.bundle_id)['answers']['worksheets'][0]
        assert kept['reviewer_support'] == 'unknown'
        for operand in kept['operands']:
            assert len(operand['selections']) == 1
            assert operand['selections'][0]['excerpt'] == passage(bundle.sources[0], 6, 6)['text']
            assert operand['selections'][0]['source_hash'] == bundle.sources[0].sha256
        page.reload()
        expect(page.locator('#worksheets .worksheet-operand').first).to_contain_text('filing L6')
        assert store.load_task(bundle.bundle_id)['answers']['worksheets'][0] == kept
        assert bundle.claims[0].calculation.formula == '(current - prior) * 10'
        browser.close()
