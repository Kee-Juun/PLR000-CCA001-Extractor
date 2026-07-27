"""
Selenium automation module for Lexis website scraping.

Security notes:
- This module never stores credentials.
- Login returns True only after a post-login-only element is found.
- The GUI decides whether to save credentials after this method succeeds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


@dataclass(frozen=True)
class LoginResult:
    """Represents the outcome of a Lexis login attempt."""

    success: bool
    reason: str
    message: str


class LexisScraper:
    """Handles Selenium automation for Lexis website."""

    LEXIS_URL = "https://plus.lexis.com/zhome?pdmfid=1530671&crid=55227f81-2f4a-4c13-bf0a-39f3db847462"

    # Login selectors
    USERID_FIELD = "//*[@id='userid']"
    SIGNIN_BUTTON = "//*[@id='signInSbmtBtn']"
    PASSWORD_FIELD = "//*[@id='password']"
    NEXT_BUTTON = "//*[@id='next']"

    # Search/document selectors
    SEARCH_TERMS_FIELD = "//*[@id='searchTerms']"
    SEARCH_BUTTON_CANDIDATES = (
        "//search-button//button",
        "//button[contains(@aria-label, 'Search')]",
        "//button[.//span[contains(normalize-space(), 'Search')]]",
    )
    RESULTS_PANEL_CANDIDATES = (
        "//ln-peersearch//searchresults",
        "//searchresults",
        "//content-type-panel",
        "//contenttypelist",
    )
    ADMIN_MATERIALS_BUTTON_CANDIDATES = (
        "//content-type-panel//contenttypelist//button[contains(normalize-space(.), 'Administrative Materials')]",
        "//content-type-panel//button[contains(normalize-space(.), 'Administrative Materials')]",
        "//contenttypelist//button[contains(normalize-space(.), 'Administrative Materials')]",
        "//button[contains(normalize-space(.), 'Administrative Materials')]",
    )
    NEW_SEARCH_BUTTON = "//*[@id='searchBoxZone']//searchbox//button | //button[contains(., 'New Search')]"
    DOCUMENT_TITLE = "//*[@id='SS_DocumentTitle']"
    POST_LOGIN_TIMEOUT = 90
    POST_LOGIN_RETRY_TIMEOUT = 60
    LOGIN_REASON_SUCCESS = "success"
    LOGIN_REASON_INVALID_CREDENTIALS = "invalid_credentials"
    LOGIN_REASON_NETWORK_OR_SITE = "network_or_site"
    LOGIN_REASON_AUTH_CHALLENGE = "auth_challenge"
    LOGIN_REASON_UNKNOWN = "unknown"
    LOGIN_REASON_SUBMIT_BUTTON_NOT_CLICKABLE = "submit_button_not_clickable"
    LOGIN_REASON_EXCEPTION = "exception"
    INVALID_CREDENTIAL_PHRASES = (
        "invalid username",
        "invalid password",
        "incorrect username",
        "incorrect password",
        "incorrect credentials",
        "wrong password",
        "unable to sign in",
        "sign in failed",
        "your id or password is incorrect",
    )
    AUTH_CHALLENGE_PHRASES = (
        "verify your identity",
        "verification code",
        "multi-factor",
        "two-factor",
        "one-time code",
        "authenticator",
        "security challenge",
        "captcha",
    )
    NETWORK_OR_SITE_PHRASES = (
        "this site can't be reached",
        "took too long to respond",
        "err_timed_out",
        "err_name_not_resolved",
        "dns_probe_finished",
        "temporarily unavailable",
        "gateway timeout",
        "service unavailable",
        "bad gateway",
    )

    def __init__(
        self,
        logger: Any | None = None,
        wait_timeout: int = 60,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.driver: webdriver.Chrome | None = None
        self.logger = logger
        self.wait_timeout = wait_timeout
        self.cancel_check = cancel_check

    def _log(self, message: str) -> None:
        """Log a message if logger is available."""
        if self.logger:
            self.logger.log(message)

    def _require_driver(self) -> webdriver.Chrome:
        """Return the active driver or raise a clear error."""
        if self.driver is None:
            raise RuntimeError("Browser has not been launched")
        return self.driver

    def _wait(self, timeout: int | None = None) -> WebDriverWait:
        """Create a WebDriverWait bound to the current driver."""
        return WebDriverWait(
            self._require_driver(),
            timeout or self.wait_timeout,
            poll_frequency=0.25,
        )

    def _raise_if_cancelled(self) -> None:
        """Abort promptly when the UI requests a stop."""
        if self.cancel_check and self.cancel_check():
            raise InterruptedError("Run stopped by user.")

    def _sleep_with_cancel(self, seconds: float) -> None:
        """Sleep in short slices so stop requests remain responsive."""
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            self._raise_if_cancelled()
            time.sleep(min(0.1, deadline - time.time()))

    def _until(self, condition, timeout: int | None = None):
        """Run a cancel-aware wait condition."""
        def wrapped(driver):
            self._raise_if_cancelled()
            return condition(driver)

        return self._wait(timeout).until(wrapped)

    def launch_browser(self, headless_mode: bool = False) -> bool:
        """
        Launch Chrome browser.

        Args:
            headless_mode: If True, run browser in headless mode.
        """
        try:
            chrome_options = Options()

            if headless_mode:
                # "new" headless mode is more compatible with modern Chrome.
                chrome_options.add_argument("--headless=new")
                chrome_options.add_argument("--window-size=1920,1080")
                self._log("Chrome browser launched in headless mode")
            else:
                chrome_options.add_argument("--start-maximized")

            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option("useAutomationExtension", False)

            self.driver = webdriver.Chrome(options=chrome_options)
            self._log("Chrome browser launched successfully")
            return True

        except WebDriverException as e:
            self._log(f"Error launching browser: {e}")
            return False

    def navigate_to_lexis(self) -> bool:
        """Navigate to Lexis website."""
        try:
            self._require_driver().get(self.LEXIS_URL)
            self._log("Navigated to Lexis website")
            return True
        except Exception as e:
            self._log(f"Error navigating to Lexis: {e}")
            return False

    def wait_for_element(self, xpath: str, timeout: int | None = None) -> bool:
        """Wait for an element to be present."""
        try:
            self._until(EC.presence_of_element_located((By.XPATH, xpath)), timeout)
            return True
        except TimeoutException:
            return False

    def wait_for_element_clickable(self, xpath: str, timeout: int | None = None) -> bool:
        """Wait for an element to be clickable."""
        try:
            self._until(EC.element_to_be_clickable((By.XPATH, xpath)), timeout)
            return True
        except TimeoutException:
            return False

    def _find_clickable(self, xpath: str, timeout: int | None = None) -> WebElement:
        """Return a clickable element for the supplied XPath."""
        return self._until(EC.element_to_be_clickable((By.XPATH, xpath)), timeout)

    def _find_present(self, xpath: str, timeout: int | None = None) -> WebElement:
        """Return a present element for the supplied XPath."""
        return self._until(EC.presence_of_element_located((By.XPATH, xpath)), timeout)

    def _wait_for_any_visible_xpath(
        self,
        xpaths: tuple[str, ...],
        timeout: int = 30,
    ) -> str | None:
        """Return the first XPath whose element becomes visible within the timeout."""
        driver = self._require_driver()
        deadline = time.time() + timeout

        while time.time() < deadline:
            self._raise_if_cancelled()
            for xpath in xpaths:
                try:
                    elements = driver.find_elements(By.XPATH, xpath)
                    if any(element.is_displayed() for element in elements):
                        return xpath
                except (StaleElementReferenceException, WebDriverException):
                    continue

            self._sleep_with_cancel(0.5)

        return None

    def _javascript_click(self, xpath: str, timeout: int | None = None) -> bool:
        """Use JavaScript to click an element when a native Selenium click fails."""
        driver = self._require_driver()

        try:
            element = self._find_present(xpath, timeout=timeout)
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});",
                element,
            )
            self._sleep_with_cancel(0.25)
            driver.execute_script("arguments[0].click();", element)
            self._log("Clicked element using JavaScript fallback")
            return True
        except Exception as e:
            self._log(f"JavaScript click fallback failed: {e}")
            return False

    def _get_debug_artifact_folder(self) -> Path:
        """Return the preferred folder for debug artifacts like screenshots."""
        if self.logger and hasattr(self.logger, "get_log_file_path"):
            try:
                log_file_path = self.logger.get_log_file_path()
                if log_file_path:
                    return Path(log_file_path).parent
            except Exception:
                pass

        return Path.cwd()

    def _save_debug_screenshot(self, label: str) -> Path | None:
        """Save a screenshot for troubleshooting and return its path."""
        safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in label)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = self._get_debug_artifact_folder() / f"{safe_label}_{timestamp}.png"

        try:
            if self._require_driver().save_screenshot(str(screenshot_path)):
                self._log(f"Saved debug screenshot: {screenshot_path}")
                return screenshot_path

            self._log("save_screenshot returned False")
        except Exception as e:
            self._log(f"Failed to save debug screenshot: {e}")

        return None

    def _log_page_diagnostics(self, context: str) -> None:
        """Log current page details and capture a screenshot for troubleshooting."""
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

        self._log(f"{context} | Current URL: {current_url}")
        self._log(f"{context} | Page title: {page_title}")
        self._log(f"{context} | document.readyState: {ready_state}")

        for xpath in self.ADMIN_MATERIALS_BUTTON_CANDIDATES:
            try:
                match_count = len(driver.find_elements(By.XPATH, xpath))
                self._log(f"{context} | Administrative Materials selector matches: {match_count} | {xpath}")
            except Exception as e:
                self._log(f"{context} | Could not count selector matches for {xpath}: {e}")

        self._save_debug_screenshot(context.lower().replace(" ", "_"))

    def wait_for_results_panel(self, timeout: int = 45) -> bool:
        """Wait for the search results panel to become visible after running a search."""
        driver = self._require_driver()
        visible_xpath = self._wait_for_any_visible_xpath(
            self.RESULTS_PANEL_CANDIDATES,
            timeout=timeout,
        )

        if visible_xpath is None:
            self._log("Search results panel did not become visible within timeout")
            self._log_page_diagnostics("Results Panel Timeout")
            return False

        try:
            self._until(
                lambda _driver: driver.execute_script("return document.readyState") == "complete",
                timeout,
            )
        except TimeoutException:
            self._log("Document readyState did not reach 'complete' before timeout")

        self._log(f"Search results panel is ready using selector: {visible_xpath}")
        self._sleep_with_cancel(1)
        return True

    def safe_click(
        self,
        xpath: str,
        timeout: int | None = None,
        retries: int = 3,
        allow_js_fallback: bool = False,
    ) -> bool:
        """Safely click an element with retry logic."""
        driver = self._require_driver()

        for attempt in range(1, retries + 1):
            try:
                element = self._find_clickable(xpath, timeout=timeout)
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    element,
                )
                self._sleep_with_cancel(0.25)
                element.click()
                return True

            except (
                TimeoutException,
                StaleElementReferenceException,
                ElementClickInterceptedException,
                WebDriverException,
            ) as e:
                if attempt == retries:
                    if allow_js_fallback and self._javascript_click(xpath, timeout=timeout):
                        return True

                    self._log(f"Failed to click element after {retries} attempts: {e}")
                    return False

                self._log(f"Click attempt {attempt}/{retries} failed; retrying...")
                self._sleep_with_cancel(1)

        return False

    def _click_first_available(
        self,
        xpaths: tuple[str, ...],
        timeout: int = 30,
        retries: int = 2,
        allow_js_fallback: bool = False,
    ) -> bool:
        """Click the first available selector from a list of XPath candidates."""
        for xpath in xpaths:
            if self.safe_click(
                xpath,
                timeout=timeout,
                retries=retries,
                allow_js_fallback=allow_js_fallback,
            ):
                return True
        return False

    def _classify_login_failure(self) -> LoginResult:
        """Classify probable login failure reason for clearer user feedback."""
        driver = self._require_driver()

        try:
            current_url = driver.current_url.lower()
        except Exception:
            current_url = ""

        try:
            page_title = driver.title.lower()
        except Exception:
            page_title = ""

        try:
            page_text = driver.page_source.lower()
        except Exception:
            page_text = ""

        if (
            current_url.startswith("chrome-error://")
            or any(token in current_url for token in self.NETWORK_OR_SITE_PHRASES)
            or any(token in page_title for token in self.NETWORK_OR_SITE_PHRASES)
            or any(token in page_text for token in self.NETWORK_OR_SITE_PHRASES)
        ):
            return LoginResult(
                success=False,
                reason=self.LOGIN_REASON_NETWORK_OR_SITE,
                message=(
                    "Could not complete Lexis login due to a network/site issue. "
                    "Please retry when connectivity is stable."
                ),
            )

        if any(token in page_text for token in self.AUTH_CHALLENGE_PHRASES):
            return LoginResult(
                success=False,
                reason=self.LOGIN_REASON_AUTH_CHALLENGE,
                message=(
                    "Additional verification was requested during login "
                    "(e.g., MFA/captcha/security challenge)."
                ),
            )

        if any(token in page_text for token in self.INVALID_CREDENTIAL_PHRASES):
            return LoginResult(
                success=False,
                reason=self.LOGIN_REASON_INVALID_CREDENTIALS,
                message="Lexis rejected the provided ID/password.",
            )

        return LoginResult(
            success=False,
            reason=self.LOGIN_REASON_UNKNOWN,
            message=(
                "Login did not complete within the expected time and no explicit "
                "error banner was detected."
            ),
        )

    def login(self, user_id: str, password: str) -> LoginResult:
        """
        Login to Lexis website.

        Returns:
            LoginResult with success flag and classified failure reason.
        """
        try:
            driver = self._require_driver()

            userid_field = self._until(
                EC.visibility_of_element_located((By.XPATH, self.USERID_FIELD))
            )
            userid_field.clear()
            userid_field.send_keys(user_id)
            self._log("User ID entered")

            if not self.safe_click(self.SIGNIN_BUTTON, timeout=15, retries=2):
                self._log("Sign In button not clickable")
                return LoginResult(
                    success=False,
                    reason=self.LOGIN_REASON_SUBMIT_BUTTON_NOT_CLICKABLE,
                    message="Could not click the Sign In button.",
                )

            password_field = self._until(
                EC.visibility_of_element_located((By.XPATH, self.PASSWORD_FIELD))
            )
            password_field.clear()
            password_field.send_keys(password)
            self._log("Password entered")

            if not self.safe_click(self.NEXT_BUTTON, timeout=15, retries=2):
                self._log("Next/Sign In button not clickable")
                return LoginResult(
                    success=False,
                    reason=self.LOGIN_REASON_SUBMIT_BUTTON_NOT_CLICKABLE,
                    message="Could not click the Next/Sign In button.",
                )

            try:
                self._until(
                    EC.presence_of_element_located((By.XPATH, self.SEARCH_TERMS_FIELD)),
                    self.POST_LOGIN_TIMEOUT,
                )
                self._log("Login successful")
                return LoginResult(
                    success=True,
                    reason=self.LOGIN_REASON_SUCCESS,
                    message="Login successful.",
                )
            except TimeoutException:
                current_url = driver.current_url
                self._log(
                    "Login is taking longer than expected; search terms field was not found. "
                    f"Current URL: {current_url}"
                )
                self._log(
                    "Retrying login readiness check for possible network latency "
                    f"(+{self.POST_LOGIN_RETRY_TIMEOUT}s)"
                )

                try:
                    self._until(
                        EC.presence_of_element_located((By.XPATH, self.SEARCH_TERMS_FIELD)),
                        self.POST_LOGIN_RETRY_TIMEOUT,
                    )
                    self._log("Login successful after extended wait")
                    return LoginResult(
                        success=True,
                        reason=self.LOGIN_REASON_SUCCESS,
                        message="Login successful after extended wait.",
                    )
                except TimeoutException:
                    self._log_page_diagnostics("Login Timeout")
                    failure_result = self._classify_login_failure()
                    self._log(
                        "Login failed after extended wait | "
                        f"reason={failure_result.reason} | detail={failure_result.message}"
                    )
                    return failure_result

        except Exception as e:
            self._log(f"Error during login: {e}")
            return LoginResult(
                success=False,
                reason=self.LOGIN_REASON_EXCEPTION,
                message=f"Unexpected error during login: {e}",
            )

    def search_lni(self, lni: str, is_first_search: bool = True) -> bool:
        """
        Search for LNI.

        Args:
            lni: LNI value without prefix.
            is_first_search: Kept for backwards compatibility.
        """
        try:
            search_query = f"lni={lni}"

            search_field = self._until(
                EC.visibility_of_element_located((By.XPATH, self.SEARCH_TERMS_FIELD))
            )
            search_field.clear()
            search_field.send_keys(search_query)
            self._log(f"Entered search query: {search_query}")

            if not self._click_first_available(self.SEARCH_BUTTON_CANDIDATES, timeout=30):
                self._log("Search button was not found or not clickable")
                return False

            self._log("Clicked search button")
            if not self.wait_for_results_panel(timeout=45):
                self._log("Search results panel was not ready after clicking search")
                return False

            return True

        except Exception as e:
            self._log(f"Error during search: {e}")
            return False

    def click_administrative_materials(self) -> bool:
        """Click the Administrative Materials results filter."""
        try:
            visible_xpath = self._wait_for_any_visible_xpath(
                self.ADMIN_MATERIALS_BUTTON_CANDIDATES,
                timeout=30,
            )

            if visible_xpath is None:
                self._log("Administrative Materials button never became visible")
                self._log_page_diagnostics("Administrative Materials Visibility Failure")
                return False

            if not self.safe_click(
                visible_xpath,
                timeout=15,
                retries=3,
                allow_js_fallback=True,
            ):
                self._log("Administrative Materials button not found or not clickable")
                self._log_page_diagnostics("Administrative Materials Click Failure")
                return False

            self._log("Clicked Administrative Materials Results button")
            self._sleep_with_cancel(3)
            return True

        except Exception as e:
            self._log(f"Error clicking Administrative Materials: {e}")
            return False

    def find_result_card(self) -> tuple[bool, WebElement | None]:
        """
        Find the result card element.

        Returns:
            Tuple of (found, element).
        """
        try:
            result_xpath = "//*[contains(@id, 'Private Letter Ruling')]"

            self._until(
                EC.presence_of_element_located((By.XPATH, result_xpath)),
                30,
            )
            self._sleep_with_cancel(1)

            elements = self._require_driver().find_elements(By.XPATH, result_xpath)

            if elements:
                self._log("Result card found")
                return True, elements[0]

            self._log("No result card found")
            return False, None

        except TimeoutException:
            self._log("No result card found within timeout")
            return False, None
        except Exception as e:
            self._log(f"Error finding result card: {e}")
            return False, None

    def click_result_card(self, element: WebElement) -> bool:
        """Click the result card."""
        try:
            driver = self._require_driver()
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});",
                element,
            )
            self._sleep_with_cancel(0.5)

            self._until(EC.element_to_be_clickable(element), 10)
            element.click()

            self._log("Clicked result card")
            self._sleep_with_cancel(3)
            return True

        except Exception as e:
            self._log(f"Error clicking result card: {e}")
            return False

    def extract_lexis_cite(self) -> str:
        """
        Extract Lexis Cite from the document.

        Returns:
            Lexis Cite text or "Not Available" if not found.
        """
        try:
            title_element = self._until(
                EC.visibility_of_element_located((By.XPATH, self.DOCUMENT_TITLE)),
                60,
            )
            lexis_cite = title_element.text.strip()

            if not lexis_cite:
                self._log("Document title was found but empty")
                return "Not Available"

            self._log(f"Extracted Lexis Cite: {lexis_cite}")
            return lexis_cite

        except Exception as e:
            self._log(f"Error extracting Lexis Cite: {e}")
            return "Not Available"

    def click_new_search(self) -> bool:
        """Click the New Search button to start a new search."""
        try:
            if not self.safe_click(self.NEW_SEARCH_BUTTON, timeout=30, retries=3):
                self._log("New Search button not found or not clickable")
                return False

            self._log("Clicked New Search button")
            self._sleep_with_cancel(2)
            return True

        except Exception as e:
            self._log(f"Error clicking New Search: {e}")
            return False

    def close_browser(self) -> None:
        """Close the browser."""
        try:
            if self.driver:
                self.driver.quit()
                self._log("Browser closed")
        except Exception as e:
            self._log(f"Error closing browser: {e}")
        finally:
            self.driver = None
