"""
IRT search-results intake for automatically building the extraction workbook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
import time
from typing import Any, Callable

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


IRT_INVENTORY_URL = "https://tcfabprod.lexisnexis.com/shared/InventoryInvoicing/"
IRT_SEARCH_INVENTORY_BUTTON_CANDIDATES = (
    "//*[@id='menu']/table/thead/tr/td[3]/h3/a",
    "//*[@id='SearchTab']//label",
    "//*[self::label or self::a][contains(normalize-space(.), 'Search Inventory')]",
)

IRT_READY_FIELD_CANDIDATES = (
    "//*[@id='documentLNISearch']",
    "//*[@id='courtSearch']",
    "//*[@id='receivedDateandTimeSearchFrom']",
    "//*[@id='courtCode']",
    "//*[@name='courtCode']",
    "//*[self::label or self::td or self::span][contains("
    "translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
    "'COURT CODE')]",
)

IRT_COURT_FIELD_CANDIDATES = (
    "//*[@id='courtSearch']",
    "//*[@name='courtSearch']",
    "//*[@id='courtCode']",
    "//*[@name='courtCode']",
    "//*[self::select or self::input][contains("
    "translate(@id, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'COURT')]",
    "//*[self::select or self::input][contains("
    "translate(@name, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'COURT')]",
    "//*[self::label or self::td or self::span][contains("
    "translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
    "'COURT CODE')]/following::*[self::select or self::input][1]",
)

IRT_START_DATE_FIELD_CANDIDATES = (
    "//*[@id='receivedDateandTimeSearchFrom']",
    "//*[@name='receivedDateandTimeSearchFrom']",
    "//*[@id='startDate']",
    "//*[@name='startDate']",
    "//*[@id='receivedDateFrom']",
    "//*[@name='receivedDateFrom']",
    "//*[@id='dateFrom']",
    "//*[@name='dateFrom']",
    "//*[self::input][contains("
    "translate(@id, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'START')]",
    "//*[self::input][contains("
    "translate(@name, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'START')]",
    "//*[self::input][contains("
    "translate(@id, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'FROM')]",
    "//*[self::input][contains("
    "translate(@name, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'FROM')]",
    "//*[self::label or self::td or self::span][contains("
    "translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
    "'START DATE')]/following::input[1]",
    "//*[self::label or self::td or self::span][contains("
    "translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
    "'DATE FROM')]/following::input[1]",
)

IRT_END_DATE_FIELD_CANDIDATES = (
    "//*[@id='receivedDateandTimeSearchTo']",
    "//*[@name='receivedDateandTimeSearchTo']",
    "//*[@id='endDate']",
    "//*[@name='endDate']",
    "//*[@id='receivedDateTo']",
    "//*[@name='receivedDateTo']",
    "//*[@id='dateTo']",
    "//*[@name='dateTo']",
    "//*[self::input][contains("
    "translate(@id, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'END')]",
    "//*[self::input][contains("
    "translate(@name, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'END')]",
    "//*[self::input][contains("
    "translate(@id, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TO')]",
    "//*[self::input][contains("
    "translate(@name, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TO')]",
    "//*[self::label or self::td or self::span][contains("
    "translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
    "'END DATE')]/following::input[1]",
    "//*[self::label or self::td or self::span][contains("
    "translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
    "'DATE TO')]/following::input[1]",
)

IRT_SEARCH_BUTTON_CANDIDATES = (
    "//*[@id='search']",
    "//button[normalize-space()='Search']",
    "//input[@type='submit' and translate(@value, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')='SEARCH']",
    "//input[@type='button' and translate(@value, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')='SEARCH']",
)

IRT_RESULTS_TABLE_CANDIDATES = (
    "//*[@id='searchTable']",
    "//table[contains(@class, 'dataTable')]",
)

TABLE_FIELD_ALIASES = {
    "LNI": ("lni", "document lni", "lexis id", "lexisnexis id"),
    "FILENAME": ("file name", "filename", "document name", "document title", "title"),
    "COURT": ("court code", "court"),
    "DECIDED DATE": (
        "decided date",
        "decision date",
        "date",
        "received date",
        "received date/time",
        "received date and time",
        "release date",
    ),
    "ROUTE": ("route",),
    "SOURCE DETAIL": ("source detail", "source details"),
}

NO_RESULTS_PHRASES = (
    "no records found",
    "no results found",
    "0 records found",
    "no inventory found",
    "total no. of records found are 0",
    "no data found",
    "showing 0 to 0 of 0 entries",
)


class IRTImportError(RuntimeError):
    """Raised when the source workbook cannot be imported from IRT."""


@dataclass(frozen=True)
class IRTQuery:
    """Represents one IRT search intake request."""

    court_scope: str
    start_date: date
    end_date: date

    def courts(self) -> tuple[str, ...]:
        normalized = str(self.court_scope or "").strip().upper()
        if normalized in {"", "BOTH", "ALL"}:
            return ("FDPLR000", "FDCCA001")
        if normalized in {"FDPLR000", "FDCCA001"}:
            return (normalized,)
        raise IRTImportError(f"Unsupported IRT court scope: {self.court_scope}")

    def format_for_form(self, value: date) -> str:
        return value.strftime("%m-%d-%Y")


@dataclass(frozen=True)
class IRTSourceRow:
    """Represents one source-data row imported from IRT."""

    lni: str
    filename: str
    court: str
    decided_date: str
    route: str = ""
    source_detail: str = ""

    def as_excel_record(self) -> dict[str, str]:
        return {
            "LNI": self.lni,
            "FILENAME": self.filename,
            "COURT": self.court,
            "DECIDED DATE": self.decided_date,
            "LEXIS CITE": "",
        }


@dataclass(frozen=True)
class IRTImportSummary:
    """Metadata for the imported IRT source workbook."""

    workbook_path: Path
    imported_row_count: int
    excluded_row_count: int
    total_result_row_count: int
    queried_courts: tuple[str, ...]
    start_date: date
    end_date: date
    selected_headers: list[str]


def _log(logger: Any | None, message: str) -> None:
    if logger:
        logger.log(message)


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    """Stop promptly when the UI requests cancellation."""
    if cancel_check and cancel_check():
        raise InterruptedError("Run stopped by user.")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split()).strip()


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean_text(value).lower()).strip()


def _format_short_date(value: date) -> str:
    return f"{value.month}/{value.day}/{value.year}"


def _normalize_date_text(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""

    patterns = (
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        raw_value = match.group(1)
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw_value, fmt).date()
                return _format_short_date(parsed)
            except ValueError:
                continue

    return text


def _classify_exclusion(filename: str, source_detail: str) -> tuple[bool, str]:
    normalized_filename = Path(str(filename or "")).name
    normalized_source_detail = str(source_detail or "").strip().lower()
    filename_lower = normalized_filename.lower()

    if "arc" in normalized_source_detail:
        return True, "Source Detail contains ARC"
    if "idx" in filename_lower:
        return True, "Filename contains idx"
    if "-" in normalized_filename:
        return True, "Filename contains hyphen"
    return False, ""


class IRTIntakeScraper:
    """Collect PLR/CCA rows from IRT Search Inventory."""

    def __init__(
        self,
        logger: Any | None = None,
        wait_timeout: int = 35,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.logger = logger
        self.wait_timeout = wait_timeout
        self.driver: webdriver.Chrome | None = None
        self.cancel_check = cancel_check

    def _require_driver(self) -> webdriver.Chrome:
        if self.driver is None:
            raise RuntimeError("IRT browser has not been launched")
        return self.driver

    def _wait(self, timeout: int | None = None) -> WebDriverWait:
        return WebDriverWait(self._require_driver(), timeout or self.wait_timeout)

    def _raise_if_cancelled(self) -> None:
        """Abort promptly when a stop request is active."""
        _raise_if_cancelled(self.cancel_check)

    def _sleep_with_cancel(self, seconds: float) -> None:
        """Sleep without making the stop button feel decorative."""
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            self._raise_if_cancelled()
            time.sleep(min(0.1, deadline - time.time()))

    def launch_browser(self, headless_mode: bool = False) -> None:
        chrome_options = Options()
        if headless_mode:
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1920,1080")
            _log(self.logger, "IRT browser launched in headless mode")
        else:
            chrome_options.add_argument("--start-maximized")

        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        self.driver = webdriver.Chrome(options=chrome_options)
        _log(self.logger, "IRT browser launched successfully")

    def close(self) -> None:
        if self.driver is None:
            return
        try:
            self.driver.quit()
            _log(self.logger, "IRT browser closed")
        finally:
            self.driver = None

    def _save_debug_screenshot(self, label: str) -> Path | None:
        safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in label)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path.cwd()
        if self.logger and hasattr(self.logger, "get_log_file_path"):
            try:
                log_path = self.logger.get_log_file_path()
                if log_path:
                    output_dir = Path(log_path).parent
            except Exception:
                pass

        screenshot_path = output_dir / f"{safe_label}_{timestamp}.png"
        try:
            if self._require_driver().save_screenshot(str(screenshot_path)):
                _log(self.logger, f"Saved IRT debug screenshot: {screenshot_path}")
                return screenshot_path
        except Exception as exc:
            _log(self.logger, f"Could not save IRT debug screenshot: {exc}")
        return None

    def _log_page_diagnostics(self, context: str) -> None:
        driver = self._require_driver()
        try:
            current_url = driver.current_url
        except Exception:
            current_url = "<unavailable>"
        try:
            page_title = driver.title
        except Exception:
            page_title = "<unavailable>"
        try:
            ready_state = driver.execute_script("return document.readyState")
        except Exception:
            ready_state = "<unavailable>"

        _log(self.logger, f"{context} | Current URL: {current_url}")
        _log(self.logger, f"{context} | Page title: {page_title}")
        _log(self.logger, f"{context} | document.readyState: {ready_state}")
        self._save_debug_screenshot(context.lower().replace(" ", "_"))

    def _find_first_displayed(self, xpaths: tuple[str, ...], timeout: int = 20) -> WebElement | None:
        driver = self._require_driver()
        deadline = time.time() + timeout

        while time.time() < deadline:
            self._raise_if_cancelled()
            for xpath in xpaths:
                try:
                    for element in driver.find_elements(By.XPATH, xpath):
                        if element.is_displayed():
                            return element
                except Exception:
                    continue
            self._sleep_with_cancel(0.4)
        return None

    def _click_first_available(self, xpaths: tuple[str, ...], timeout: int = 20) -> bool:
        element = self._find_first_displayed(xpaths, timeout=timeout)
        if element is None:
            return False

        driver = self._require_driver()
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            self._sleep_with_cancel(0.2)
            element.click()
            return True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                return False

    def navigate_to_inventory(self) -> None:
        self._require_driver().get(IRT_INVENTORY_URL)
        _log(self.logger, f"Navigated to IRT Inventory: {IRT_INVENTORY_URL}")

    def open_search_inventory(self) -> None:
        if self._find_first_displayed(IRT_READY_FIELD_CANDIDATES, timeout=4) is not None:
            _log(self.logger, "IRT Search Inventory is already ready")
            return

        if not self._click_first_available(IRT_SEARCH_INVENTORY_BUTTON_CANDIDATES, timeout=25):
            self._log_page_diagnostics("IRT Search Inventory Click Failure")
            raise IRTImportError("Could not click the IRT 'Search Inventory' menu.")

        _log(self.logger, "Clicked IRT Search Inventory")
        if self._find_first_displayed(IRT_READY_FIELD_CANDIDATES, timeout=20) is None:
            self._log_page_diagnostics("IRT Search Inventory Readiness Failure")
            raise IRTImportError("IRT Search Inventory did not finish loading.")

        _log(self.logger, "IRT Search Inventory is ready")

    def _set_control_value(self, xpaths: tuple[str, ...], value: str, label: str) -> None:
        element = self._find_first_displayed(xpaths, timeout=15)
        if element is None:
            self._log_page_diagnostics(f"IRT {label} Field Missing")
            raise IRTImportError(f"Could not find the IRT {label} field.")

        driver = self._require_driver()
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        self._sleep_with_cancel(0.2)

        tag_name = (element.tag_name or "").lower()
        if tag_name == "select":
            try:
                Select(element).select_by_visible_text(value)
                _log(self.logger, f"Selected IRT {label}: {value}")
                return
            except Exception:
                for option in element.find_elements(By.TAG_NAME, "option"):
                    option_text = _clean_text(option.text)
                    option_value = _clean_text(option.get_attribute("value"))
                    if value in {option_text, option_value}:
                        option.click()
                        _log(self.logger, f"Selected IRT {label}: {value}")
                        return
                raise IRTImportError(f"Could not select '{value}' in the IRT {label} field.")

        if "date" in label.lower():
            try:
                driver.execute_script(
                    """
                    const element = arguments[0];
                    const value = arguments[1];
                    element.focus();
                    element.value = value;
                    element.setAttribute("value", value);
                    element.dispatchEvent(new Event("input", { bubbles: true }));
                    element.dispatchEvent(new Event("change", { bubbles: true }));
                    element.dispatchEvent(new Event("blur", { bubbles: true }));
                    """,
                    element,
                    value,
                )
                read_back_value = _clean_text(element.get_attribute("value"))
                if read_back_value != value:
                    raise IRTImportError(
                        f"IRT {label} did not retain the expected date format. "
                        f"Expected '{value}', found '{read_back_value or '<blank>'}'."
                    )
                _log(self.logger, f"Entered IRT {label}: {value}")
                return
            except Exception as exc:
                self._log_page_diagnostics(f"IRT {label} Date Formatting Failure")
                raise IRTImportError(f"Could not set the IRT {label} to {value}: {exc}") from exc

        try:
            element.clear()
        except Exception:
            pass
        try:
            element.send_keys(value)
        except Exception:
            driver.execute_script("arguments[0].value = arguments[1];", element, value)
        _log(self.logger, f"Entered IRT {label}: {value}")

    def _current_results_signature(self) -> str:
        """Capture a lightweight signature of the currently visible search results."""
        table = self._find_first_displayed(IRT_RESULTS_TABLE_CANDIDATES, timeout=2)
        if table is None:
            return ""

        try:
            row_text = _clean_text(table.text)
        except Exception:
            row_text = ""

        try:
            count_label = _clean_text(
                self._require_driver().find_element(By.ID, "searchCount").text
            )
        except Exception:
            count_label = ""

        return f"{count_label}|{row_text[:600]}"

    def _inventory_ajax_state(self) -> tuple[int, bool]:
        """Return the live IRT ajax/request state when the page exposes it."""
        try:
            state = self._require_driver().execute_script(
                """
                const jqueryActive =
                    typeof window.jQuery !== "undefined" && window.jQuery
                        ? Number(window.jQuery.active || 0)
                        : 0;
                const blockUiVisible = Boolean(
                    Array.from(document.querySelectorAll("div.blockUI, div.blockOverlay")).find(
                        (node) => {
                            const style = window.getComputedStyle(node);
                            return style && style.display !== "none" && style.visibility !== "hidden";
                        }
                    )
                );
                return { jqueryActive, blockUiVisible };
                """
            )
            jquery_active = int((state or {}).get("jqueryActive", 0))
            block_ui_visible = bool((state or {}).get("blockUiVisible", False))
            return jquery_active, block_ui_visible
        except Exception:
            return 0, False

    def _force_inventory_search(self) -> bool:
        """Invoke the IRT search javascript directly when button clicks are not enough."""
        try:
            triggered = bool(
                self._require_driver().execute_script(
                    """
                    if (typeof getInventorySearch === "function") {
                        getInventorySearch(1);
                        return true;
                    }
                    const button = document.getElementById("search");
                    if (button) {
                        button.click();
                        return true;
                    }
                    return false;
                    """
                )
            )
            if triggered:
                _log(self.logger, "Triggered IRT search using javascript fallback")
            return triggered
        except Exception as exc:
            _log(self.logger, f"Could not trigger IRT search using javascript fallback: {exc}")
            return False

    def _results_show_no_data(self) -> bool:
        """Check whether the visible result area explicitly reports zero rows."""
        driver = self._require_driver()
        markers: list[str] = []

        for element_id in ("searchTable_info", "searchCount", "searchTable_wrapper", "searchTable"):
            try:
                markers.append(_clean_text(driver.find_element(By.ID, element_id).text).lower())
            except Exception:
                continue

        combined = " | ".join(marker for marker in markers if marker)
        return any(phrase in combined for phrase in NO_RESULTS_PHRASES)

    def run_search(self, court_code: str, start_date: date, end_date: date) -> list[IRTSourceRow]:
        formatted_start = start_date.strftime("%m-%d-%Y")
        formatted_end = end_date.strftime("%m-%d-%Y")
        previous_signature = self._current_results_signature()
        self._set_control_value(IRT_COURT_FIELD_CANDIDATES, court_code, "Court Code")
        self._set_control_value(IRT_START_DATE_FIELD_CANDIDATES, formatted_start, "Start Date")
        self._set_control_value(IRT_END_DATE_FIELD_CANDIDATES, formatted_end, "End Date")

        if not self._click_first_available(IRT_SEARCH_BUTTON_CANDIDATES, timeout=15):
            self._log_page_diagnostics("IRT Search Button Failure")
            raise IRTImportError("Could not click the IRT Search button.")

        saw_activity = False
        post_click_deadline = time.time() + 2.5
        while time.time() < post_click_deadline:
            self._raise_if_cancelled()
            jquery_active, block_ui_visible = self._inventory_ajax_state()
            if jquery_active > 0 or block_ui_visible:
                saw_activity = True
                break
            self._sleep_with_cancel(0.15)

        if not saw_activity:
            self._force_inventory_search()

        _log(
            self.logger,
            f"Ran IRT search for court={court_code} start={formatted_start} end={formatted_end}",
        )
        self._wait_for_results(court_code, previous_signature=previous_signature)
        return self._extract_source_rows(current_court=court_code)

    def _wait_for_results(
        self,
        court_code: str,
        timeout: int = 45,
        previous_signature: str = "",
    ) -> None:
        deadline = time.time() + timeout
        driver = self._require_driver()
        saw_request_activity = False

        while time.time() < deadline:
            self._raise_if_cancelled()
            try:
                ready_state = driver.execute_script("return document.readyState")
                if ready_state != "complete":
                    self._sleep_with_cancel(0.4)
                    continue
            except Exception:
                pass

            jquery_active, block_ui_visible = self._inventory_ajax_state()
            if jquery_active > 0 or block_ui_visible:
                saw_request_activity = True
                self._sleep_with_cancel(0.3)
                continue

            page_text = _clean_text(driver.find_element(By.TAG_NAME, "body").text).lower()
            if any(phrase in page_text for phrase in NO_RESULTS_PHRASES):
                _log(self.logger, f"IRT returned no results for {court_code}")
                return

            if saw_request_activity and self._results_show_no_data():
                _log(self.logger, f"IRT returned no results for {court_code}")
                return

            table, _headers = self._find_best_results_table()
            if table is not None:
                try:
                    signature = self._current_results_signature()
                except Exception:
                    signature = ""

                table_text = ""
                try:
                    table_text = _clean_text(table.text).upper()
                except Exception:
                    pass

                if (
                    not previous_signature
                    or signature != previous_signature
                    or court_code.upper() in table_text
                ):
                    _log(self.logger, f"IRT results table is ready for {court_code}")
                    return

                if saw_request_activity and self._results_show_no_data():
                    _log(self.logger, f"IRT returned no results for {court_code}")
                    return

            self._sleep_with_cancel(0.5)

        self._log_page_diagnostics("IRT Results Wait Timeout")
        raise IRTImportError("IRT search results did not finish loading within the timeout.")

    def _find_best_results_table(self) -> tuple[WebElement | None, list[str]]:
        prioritized_table = self._find_first_displayed(IRT_RESULTS_TABLE_CANDIDATES, timeout=2)
        if prioritized_table is not None:
            headers = self._extract_table_headers(prioritized_table)
            if self._score_table_headers(headers, prioritized_table) is not None:
                return prioritized_table, headers

        best_table: WebElement | None = None
        best_headers: list[str] = []
        best_score: tuple[int, int, int] | None = None

        for table in self._require_driver().find_elements(By.XPATH, "//table"):
            try:
                headers = self._extract_table_headers(table)
                if not headers:
                    continue

                score = self._score_table_headers(headers, table)
                if score is None:
                    continue

                if best_score is None or score > best_score:
                    best_table = table
                    best_headers = headers
                    best_score = score
            except Exception:
                continue

        return best_table, best_headers

    def _extract_table_headers(self, table: WebElement) -> list[str]:
        """Normalize all non-empty header cells from the table head."""
        header_cells = table.find_elements(By.XPATH, ".//thead//th|.//thead//td")
        if not header_cells:
            header_cells = table.find_elements(By.XPATH, ".//tr[1]/*")
        return [_normalize_header(cell.text) for cell in header_cells if _clean_text(cell.text)]

    def _score_table_headers(self, headers: list[str], table: WebElement) -> tuple[int, int, int] | None:
        header_set = set(headers)
        essential_hits = 0
        alias_hits = 0

        for aliases in TABLE_FIELD_ALIASES.values():
            hit = next((alias for alias in aliases if alias in header_set), None)
            if hit:
                alias_hits += 1
                if aliases is not TABLE_FIELD_ALIASES["SOURCE DETAIL"]:
                    essential_hits += 1

        if essential_hits < 2:
            return None

        row_count = 0
        try:
            row_count = len(table.find_elements(By.XPATH, ".//tbody/tr|.//tr[position()>1]"))
        except Exception:
            pass

        return (essential_hits, alias_hits, row_count)

    def _extract_source_rows(self, current_court: str) -> list[IRTSourceRow]:
        table, headers = self._find_best_results_table()
        if table is None:
            return []

        header_indices = {header: index for index, header in enumerate(headers)}
        extracted_rows: list[IRTSourceRow] = []
        body_rows = table.find_elements(By.XPATH, "./tbody/tr")
        if not body_rows:
            body_rows = table.find_elements(By.XPATH, ".//tr[position()>1]")

        for table_row in body_rows:
            cells = table_row.find_elements(By.XPATH, "./td|./th")
            if not cells:
                continue

            values = [_clean_text(cell.text) for cell in cells]
            if not any(values):
                continue

            normalized_values = [_normalize_header(value) for value in values if value]
            if normalized_values and normalized_values == headers[: len(normalized_values)]:
                continue
            if len(values) == 1 and (
                _normalize_header(values[0]) in {_normalize_header(phrase) for phrase in NO_RESULTS_PHRASES}
                or _normalize_header(values[0]) == "loading"
            ):
                continue

            row_map = {
                header: values[index] if index < len(values) else ""
                for header, index in header_indices.items()
            }

            lni = self._first_field_value(row_map, "LNI")
            filename = self._first_field_value(row_map, "FILENAME")
            court = self._first_field_value(row_map, "COURT") or current_court
            decided_date = _normalize_date_text(self._first_field_value(row_map, "DECIDED DATE"))
            route = self._first_field_value(row_map, "ROUTE")
            source_detail = self._first_field_value(row_map, "SOURCE DETAIL")

            if not any((lni, filename, court, decided_date, route, source_detail)):
                continue

            if not lni or not filename:
                continue

            extracted_rows.append(
                IRTSourceRow(
                    lni=lni,
                    filename=filename,
                    court=court,
                    decided_date=decided_date,
                    route=route,
                    source_detail=source_detail,
                )
            )

        _log(self.logger, f"Extracted {len(extracted_rows)} raw IRT result row(s) for {current_court}")
        return extracted_rows

    def _first_field_value(self, row_map: dict[str, str], field_name: str) -> str:
        for alias in TABLE_FIELD_ALIASES[field_name]:
            for header, value in row_map.items():
                if header == alias:
                    return _clean_text(value)
        return ""


def import_irt_results_to_workbook(
    run_folder: Path,
    query: IRTQuery,
    logger: Any | None = None,
    header_fill_color: str | None = None,
    headless_mode: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> IRTImportSummary:
    """
    Query IRT Search Inventory and build a clean workbook from the result rows.
    """
    from utils.excel_handler import ExcelHandler

    run_folder.mkdir(parents=True, exist_ok=True)
    scraper = IRTIntakeScraper(logger=logger, cancel_check=cancel_check)

    raw_rows: list[IRTSourceRow] = []
    selected_headers: list[str] = []

    try:
        _raise_if_cancelled(cancel_check)
        scraper.launch_browser(headless_mode=headless_mode)
        scraper.navigate_to_inventory()
        scraper.open_search_inventory()

        for court_code in query.courts():
            _raise_if_cancelled(cancel_check)
            _log(
                logger,
                "IRT intake filters: "
                f"court={court_code} start={query.format_for_form(query.start_date)} "
                f"end={query.format_for_form(query.end_date)}",
            )
            court_rows = scraper.run_search(
                court_code=court_code,
                start_date=query.start_date,
                end_date=query.end_date,
            )
            raw_rows.extend(court_rows)
            if not selected_headers:
                table, headers = scraper._find_best_results_table()
                if table is not None and headers:
                    selected_headers = headers
    finally:
        scraper.close()

    deduped_rows: list[IRTSourceRow] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    for row in raw_rows:
        _raise_if_cancelled(cancel_check)
        key = (row.lni, row.filename, row.court, row.decided_date)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_rows.append(row)

    if not deduped_rows:
        raise IRTImportError(
            "IRT search completed, but no rows matched the selected court/date filters."
        )

    filtered_rows: list[IRTSourceRow] = []
    excluded_row_count = 0

    for row in deduped_rows:
        _raise_if_cancelled(cancel_check)
        normalized_route = str(row.route or "").strip().lower()
        if "arc" in normalized_route:
            is_excluded = True
            exclusion_reason = "Route contains ARC"
        else:
            is_excluded, exclusion_reason = _classify_exclusion(row.filename, row.source_detail)
        if is_excluded:
            excluded_row_count += 1
            _log(
                logger,
                "Excluded IRT row: "
                f"LNI={row.lni} FILE={row.filename} COURT={row.court} "
                f"ROUTE={row.route or '<blank>'} "
                f"SOURCE DETAIL={row.source_detail or '<blank>'} REASON={exclusion_reason}",
            )
            continue
        filtered_rows.append(row)

    if not filtered_rows:
        raise IRTImportError(
            "IRT search completed, but no non-excluded rows were available to import."
        )

    excel_handler = ExcelHandler(header_fill_color=header_fill_color)
    workbook_path = excel_handler.create_template(run_folder)
    _raise_if_cancelled(cancel_check)
    excel_handler.open_excel_file(workbook_path)
    excel_handler.populate_source_rows([row.as_excel_record() for row in filtered_rows])
    _raise_if_cancelled(cancel_check)
    excel_handler.save(workbook_path)

    _log(
        logger,
        f"Imported {len(filtered_rows)} row(s) from IRT results "
        f"after excluding {excluded_row_count} row(s).",
    )

    return IRTImportSummary(
        workbook_path=workbook_path,
        imported_row_count=len(filtered_rows),
        excluded_row_count=excluded_row_count,
        total_result_row_count=len(deduped_rows),
        queried_courts=query.courts(),
        start_date=query.start_date,
        end_date=query.end_date,
        selected_headers=selected_headers,
    )
