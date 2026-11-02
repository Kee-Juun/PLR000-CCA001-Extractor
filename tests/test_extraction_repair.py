"""Regression checks for the failed navigation and misleading output cascade."""
import uuid
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openpyxl

from automation.extraction_flow import extract_lni
from automation.lexis_scraper import LexisScraper
from utils.extraction_result import (
    EXTRACTED, NO_RESULTS, UNVERIFIED, NOT_CHECKED, ExtractionResult, classify_saved_result,
)
from utils.outlook_mailer import IncompleteExtractionError, prepare_extraction_email, prepare_preaudit_email


class BrowserFlowTests(unittest.TestCase):
    def test_hidden_duplicate_search_field_is_skipped(self):
        scraper = LexisScraper()
        hidden, visible = MagicMock(), MagicMock()
        hidden.is_displayed.return_value = False
        visible.is_displayed.return_value = visible.is_enabled.return_value = True
        scraper.driver = MagicMock()
        scraper.driver.find_elements.return_value = [hidden, visible]
        self.assertIs(scraper._find_clickable(scraper.SEARCH_TERMS_FIELD, 1), visible)

    def test_failed_new_search_click_recovers_home(self):
        scraper = LexisScraper()
        scraper.driver = MagicMock()
        scraper._visible_element = MagicMock(return_value=None)
        scraper.click_new_search = MagicMock(return_value=False)
        scraper._log_page_diagnostics = MagicMock()
        scraper._until = MagicMock(return_value=object())
        self.assertTrue(scraper.prepare_search())
        scraper.driver.get.assert_called_once_with(scraper.LEXIS_URL)

    def test_navigation_timeout_accepts_usable_controls(self):
        from selenium.common.exceptions import TimeoutException
        scraper = LexisScraper()
        scraper.driver = MagicMock()
        scraper.driver.get.side_effect = TimeoutException()
        scraper._until = MagicMock(return_value=object())
        self.assertTrue(scraper.navigate_to_lexis())

    def test_navigation_timeout_without_controls_fails(self):
        from selenium.common.exceptions import TimeoutException
        scraper = LexisScraper()
        scraper.driver = MagicMock()
        scraper.driver.get.side_effect = TimeoutException()
        scraper._until = MagicMock(side_effect=TimeoutException())
        scraper._log_page_diagnostics = MagicMock()
        self.assertFalse(scraper.navigate_to_lexis())

    def test_click_alone_is_not_successful_navigation(self):
        from selenium.common.exceptions import TimeoutException
        scraper = LexisScraper()
        scraper.safe_click = MagicMock(return_value=True)
        scraper._until = MagicMock(side_effect=TimeoutException())
        self.assertFalse(scraper.click_new_search())

    def test_retry_forces_fresh_search_page(self):
        scraper = MagicMock()
        scraper.prepare_search.return_value = True
        scraper.extract_current_lni.side_effect = [
            ExtractionResult(UNVERIFIED, detail="search failed"),
            ExtractionResult(EXTRACTED, "2026 PLR LEXIS 500"),
        ]
        self.assertEqual(extract_lni(scraper, "AAA-BBB").status, EXTRACTED)
        self.assertEqual([call.kwargs for call in scraper.prepare_search.call_args_list],
                         [{"force_reset": False}, {"force_reset": True}])

    def test_cancellation_escapes_retries(self):
        scraper = MagicMock()
        scraper.prepare_search.side_effect = InterruptedError()
        with self.assertRaises(InterruptedError):
            extract_lni(scraper, "AAA-BBB")
        self.assertEqual(scraper.prepare_search.call_count, 1)

    def test_missing_card_is_not_a_confirmed_negative(self):
        scraper = LexisScraper()
        scraper.search_lni = MagicMock(return_value=True)
        scraper._visible_element = MagicMock(return_value=None)
        scraper.click_administrative_materials = MagicMock(return_value=True)
        scraper.find_result_card = MagicMock(return_value=(False, None))
        scraper._log_page_diagnostics = MagicMock()
        self.assertEqual(scraper.extract_current_lni("AAA-BBB").status, UNVERIFIED)

    def test_explicit_no_results_is_a_confirmed_negative(self):
        scraper = LexisScraper()
        scraper.search_lni = MagicMock(return_value=True)
        scraper.click_administrative_materials = MagicMock(return_value=True)
        scraper._visible_element = MagicMock(return_value=object())
        self.assertEqual(scraper.extract_current_lni("AAA-BBB").status, NO_RESULTS)
        scraper.click_administrative_materials.assert_called_once()

    def test_search_query_in_url_is_not_document_identity(self):
        scraper = self.document_scraper("https://plus.lexis.com/document/?query=lni%3DAAA-BBB")
        self.assertEqual(scraper.extract_current_lni("AAA-BBB").status, UNVERIFIED)

    def test_cca_document_url_and_citation_are_accepted(self):
        scraper = self.document_scraper("https://plus.lexis.com/document/?pddocfullpath=/shared/document/urn:contentItem:AAA-BBB")
        result = scraper.extract_current_lni("AAA-BBB")
        self.assertEqual((result.status, result.cite), (EXTRACTED, "2026 IRS CCA LEXIS 42"))

    def document_scraper(self, url):
        scraper = LexisScraper()
        scraper.driver = MagicMock()
        scraper.driver.window_handles = ["main"]
        scraper.driver.current_url = url
        scraper.driver.find_elements.return_value = []
        scraper.search_lni = MagicMock(return_value=True)
        scraper.click_administrative_materials = MagicMock(return_value=True)
        scraper._visible_element = MagicMock(return_value=None)
        card = MagicMock()
        card.get_attribute.return_value = "/document/other-id"
        scraper.find_result_card = MagicMock(return_value=(True, card))
        scraper.click_result_card = MagicMock(return_value=True)
        scraper._until = MagicMock(return_value=SimpleNamespace(text="Chief Counsel Advice, 2026 IRS CCA LEXIS 42"))
        scraper._log_page_diagnostics = MagicMock()
        return scraper

    def test_cca_is_included_in_card_selectors(self):
        self.assertIn("Chief Counsel", LexisScraper.RESULT_CARD)

    def test_custom_editable_search_field_has_no_value_attribute(self):
        scraper, field = self.search_scraper("expandingtextarea")
        self.assertTrue(scraper.search_lni("AAA-BBB"))
        field.send_keys.assert_called_once_with("lni=AAA-BBB")

    def test_inline_search_input_submits_with_enter(self):
        from selenium.webdriver.common.keys import Keys
        scraper, field = self.search_scraper("input")
        self.assertTrue(scraper.search_lni("AAA-BBB"))
        self.assertEqual([call.args[0] for call in field.send_keys.call_args_list], ["lni=AAA-BBB", Keys.ENTER])

    def search_scraper(self, tag):
        scraper = LexisScraper()
        scraper.driver = MagicMock()
        scraper.driver.find_elements.return_value = []
        field = MagicMock()
        field.tag_name = tag
        field.find_elements.return_value = []
        values = {"value": "lni=AAA-BBB" if tag == "input" else None,
                  "textContent": "lni=AAA-BBB", "placeholder": "Search Cases, Legislations, Secondary Materials"}
        field.get_attribute.side_effect = values.get
        scraper._until = MagicMock(return_value=field)
        scraper._click_first_available = MagicMock(return_value=True)
        scraper.wait_for_results_panel = MagicMock(return_value=True)
        return scraper, field


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path.cwd() / "tmp" / ("test_extraction_" + uuid.uuid4().hex)
        self.folder.mkdir(parents=True)
        self.path = self.folder / "sample.xlsx"

    def tearDown(self):
        assert self.folder.resolve().parent == (Path.cwd() / 'tmp').resolve()
        for file in self.folder.iterdir():
            if file.is_dir():
                for child in file.iterdir():
                    child.unlink()
                file.rmdir()
            else:
                file.unlink()
        self.folder.rmdir()

    def make_workbook(self, values):
        workbook = openpyxl.Workbook()
        workbook.active.append(["LNI", "FILENAME", "COURT", "DECIDED DATE", "LEXIS CITE"])
        for index, value in enumerate(values):
            workbook.active.append([f"AAA-BB{index}", "file.pdf", "FDPLR000", "2026-10-23", value])
        workbook.save(self.path)
        workbook.close()

    def test_legacy_failures_block_email(self):
        self.make_workbook(["2026 PLR LEXIS 500", "Not Available"])
        with self.assertRaises(IncompleteExtractionError):
            prepare_extraction_email(self.path)

    def test_non_citation_text_is_not_success(self):
        for value in ("Stuck in Keying", "Unverified", "Not Available", "Private Letter Ruling", ""):
            self.assertEqual(classify_saved_result(value), UNVERIFIED)

    def test_confirmed_negative_does_not_claim_keying_status(self):
        from utils.excel_handler import ExcelHandler
        self.make_workbook(["", ""])
        handler = ExcelHandler()
        handler.open_excel_file(self.path)
        handler.write_extraction_result(2, ExtractionResult(EXTRACTED, "2026 PLR LEXIS 500"))
        handler.write_extraction_result(3, ExtractionResult(NO_RESULTS))
        handler.save()
        handler.workbook.close()
        summary = prepare_extraction_email(self.path)
        self.assertEqual((summary.success_count, summary.unavailable_count), (1, 1))
        self.assertNotIn("keying", summary.intro_text.lower())
        self.assertIn("no results", summary.intro_text)

    def run_worker(self, outcomes, diagnostic_mode=True, audit_mode="post", pre_error=None, post_error=None):
        from gui.main_window import ExtractionThread
        self.make_workbook([""] * len(outcomes))
        scraper = MagicMock()
        scraper.launch_browser.return_value = scraper.navigate_to_lexis.return_value = True
        scraper.login.return_value = SimpleNamespace(success=True)
        scraper.prepare_search.return_value = True
        # Technical outcomes consume two attempts each.
        scraper.extract_current_lni.side_effect = [result for result in outcomes
                                                  for _ in range(2 if result.status == UNVERIFIED else 1)]
        worker = ExtractionThread("user", "password", self.path, MagicMock(), MagicMock(),
                                  diagnostic_mode=diagnostic_mode, audit_mode=audit_mode,
                                  manual_override_to="tester@example.invalid")
        finished = []
        events = []
        scraper.launch_browser.side_effect = lambda **_kwargs: events.append("browser") or True

        def post_send(path, *_args, **_kwargs):
            events.append("post")
            if post_error:
                raise post_error
            return prepare_extraction_email(path)

        def pre_send(path, *_args, **_kwargs):
            events.append("pre")
            if pre_error:
                raise pre_error
            return prepare_preaudit_email(path)

        worker.finished.connect(lambda *args: finished.append(args))
        with patch("automation.lexis_scraper.LexisScraper", return_value=scraper), \
             patch("gui.main_window.clear_credentials") as clear, \
             patch("utils.outlook_mailer.send_extraction_email", side_effect=post_send) as post, \
             patch("utils.outlook_mailer.send_preaudit_email", side_effect=pre_send) as pre, \
             patch("utils.run_status_reporter.send_run_status_report") as report, \
             patch("utils.outlook_intake.find_conversion_email_context"):
            worker.run()
            if diagnostic_mode:
                for sender in (post, pre, report, clear):
                    sender.assert_not_called()
            else:
                pre.assert_called_once() if audit_mode in {"both", "pre"} else pre.assert_not_called()
                if audit_mode == "pre" or any(result.status == UNVERIFIED for result in outcomes):
                    post.assert_not_called()
                else:
                    post.assert_called_once()
                    self.assertEqual(post.call_args.kwargs['override_to'], 'tester@example.invalid')
                report.assert_called_once()
                self.assertEqual(report.call_args.kwargs['status'], 'success' if finished[0][0] else 'failure')
            self.worker_events = events
        return finished, scraper

    def test_circuit_breaker_preserves_unchecked_rows(self):
        outcomes = [ExtractionResult(EXTRACTED, "2026 PLR LEXIS 500")] + [ExtractionResult(UNVERIFIED)] * 6
        finished, scraper = self.run_worker(outcomes)
        self.assertFalse(finished[0][0])
        self.assertIn("3 unverified; 3 not checked", finished[0][1])
        self.assertEqual(scraper.extract_current_lni.call_count, 7)
        workbook = openpyxl.load_workbook(finished[0][2])
        self.assertEqual([workbook.active[f'F{row}'].value for row in range(2, 9)],
                         [EXTRACTED] + [UNVERIFIED] * 3 + [NOT_CHECKED] * 3)
        workbook.close()

    def test_diagnostic_mode_suppresses_all_email_in_both_audits(self):
        finished, _ = self.run_worker([ExtractionResult(EXTRACTED, "2026 PLR LEXIS 500")], audit_mode="both")
        self.assertTrue(finished[0][0])
        self.assertIn("all email disabled", finished[0][1])
        original = openpyxl.load_workbook(self.path)
        self.assertIsNone(original.active['E2'].value)
        original.close()

    def test_normal_partial_run_cannot_send_availability_email(self):
        finished, _ = self.run_worker([ExtractionResult(UNVERIFIED)], diagnostic_mode=False)
        self.assertFalse(finished[0][0])

    def test_normal_postcheck_saves_normal_workbook_then_prepares_email(self):
        finished, _ = self.run_worker([ExtractionResult(EXTRACTED, '2026 PLR LEXIS 500')], diagnostic_mode=False)
        self.assertTrue(finished[0][0])
        self.assertEqual(finished[0][2], self.path)
        self.assertEqual(self.worker_events, ['browser', 'post'])
        self.assertIn('1/1 extracted', finished[0][1])
        self.assertEqual(prepare_extraction_email(self.path).success_count, 1)
        self.assertFalse(list(self.folder.glob('Diagnostic_*')))

    def test_normal_precheck_does_not_launch_lexis(self):
        finished, scraper = self.run_worker([ExtractionResult(EXTRACTED, '2026 PLR LEXIS 500')], diagnostic_mode=False, audit_mode='pre')
        self.assertTrue(finished[0][0])
        self.assertEqual(self.worker_events, ['pre'])
        scraper.launch_browser.assert_not_called()

    def test_normal_combined_check_sends_in_selected_order(self):
        finished, _ = self.run_worker([ExtractionResult(EXTRACTED, '2026 PLR LEXIS 500')], diagnostic_mode=False, audit_mode='both')
        self.assertTrue(finished[0][0])
        self.assertEqual(self.worker_events, ['pre', 'browser', 'post'])

    def test_failed_precheck_is_not_reported_as_complete_after_postcheck(self):
        finished, _ = self.run_worker([ExtractionResult(EXTRACTED, '2026 PLR LEXIS 500')], diagnostic_mode=False,
                                      audit_mode='both', pre_error=RuntimeError('pre send failed'))
        self.assertFalse(finished[0][0])
        self.assertIn('pre send failed', finished[0][1])
        self.assertEqual(self.worker_events, ['pre', 'browser', 'post'])

    def test_failed_precheck_only_stops_before_lexis(self):
        finished, scraper = self.run_worker([ExtractionResult(EXTRACTED, '2026 PLR LEXIS 500')], diagnostic_mode=False,
                                            audit_mode='pre', pre_error=RuntimeError('pre send failed'))
        self.assertFalse(finished[0][0])
        scraper.launch_browser.assert_not_called()

    def test_failed_postcheck_retains_results_and_reports_failure(self):
        finished, _ = self.run_worker([ExtractionResult(EXTRACTED, '2026 PLR LEXIS 500')], diagnostic_mode=False,
                                      post_error=RuntimeError('post send failed'))
        self.assertFalse(finished[0][0])
        self.assertIn('post send failed', finished[0][1])
        self.assertIn('1/1 extracted', finished[0][1])
        self.assertEqual(prepare_extraction_email(self.path).success_count, 1)

    def test_full_recheck_preserves_original_and_only_checks_unresolved_rows(self):
        import diagnose_extraction
        self.make_workbook(["2026 PLR LEXIS 500", "Not Available", "Not Available"])
        original_bytes = self.path.read_bytes()
        scraper = MagicMock()
        scraper.login.return_value = SimpleNamespace(success=True)
        scraper.extract_current_lni.return_value = ExtractionResult(EXTRACTED, "2026 PLR LEXIS 527", "Document matched by LNI")
        with patch('sys.argv', ['diagnose_extraction.py', str(self.path), '--unresolved', '--headless', '--output-dir', str(self.folder)]), \
             patch('diagnose_extraction.load_credentials', return_value=SimpleNamespace(username='user', password='test')), \
             patch('diagnose_extraction.LexisScraper', return_value=scraper), \
             patch('diagnose_extraction.Logger'):
            self.assertEqual(diagnose_extraction.main(), 0)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(scraper.extract_current_lni.call_count, 2)
        output = next(self.folder.glob('*/Rechecked_*.xlsx'))
        workbook = openpyxl.load_workbook(output)
        self.assertEqual([workbook.active[f'F{row}'].value for row in range(2, 5)], [EXTRACTED] * 3)
        self.assertIn('Prior citation retained', workbook.active['G2'].value)
        workbook.close()


if __name__ == "__main__":
    unittest.main()
