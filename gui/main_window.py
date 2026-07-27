"""
Main GUI window module using PyQt6.

Security update:
- Credentials are saved only after Lexis login succeeds.
- Password storage is delegated to config.credentials, which should use the OS keyring.
- Plaintext credentials.json is no longer used.
"""

from __future__ import annotations

import traceback
from datetime import date, timedelta
from pathlib import Path

from PyQt6.QtCore import QDate, QPoint, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config.app_settings import HEADER_FILL_COLOR_KEY, load_setting, save_setting
from config.credentials import clear_credentials, load_credentials, save_credentials


class AnimatedProgressBar(QProgressBar):
    """Progress bar with sheen loading animation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sheen_position = 0
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_sheen)
        self.animation_timer.start(20)

    def update_sheen(self):
        """Update the sheen animation position."""
        if self.maximum() > 0 and self.value() > 0:
            progress_width = int((self.value() / self.maximum()) * self.width())
            max_position = max(progress_width + 100, 200)
            self.sheen_position = (self.sheen_position + 5) % max_position
        else:
            self.sheen_position = 0
        self.update()

    def paintEvent(self, event):
        """Custom paint event for sheen animation."""
        super().paintEvent(event)

        if self.value() > 0 and self.maximum() > 0:
            progress_ratio = self.value() / self.maximum()
            progress_width = int(progress_ratio * self.width())

            if progress_width > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)

                filled_rect = self.rect()
                filled_rect.setWidth(progress_width)

                gradient = QLinearGradient(
                    self.sheen_position - 50,
                    0,
                    self.sheen_position + 50,
                    0,
                )
                gradient.setColorAt(0, QColor(255, 255, 255, 0))
                gradient.setColorAt(0.5, QColor(255, 255, 255, 100))
                gradient.setColorAt(1, QColor(255, 255, 255, 0))

                painter.fillRect(filled_rect, gradient)


class ExtractionThread(QThread):
    """Thread for running extraction process without blocking GUI."""

    MAX_LNI_ATTEMPTS = 2
    progress_update = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, object)
    cancelled = pyqtSignal(str, object)

    def __init__(
        self,
        user_id: str,
        password: str,
        excel_path: Path | None,
        logger,
        file_manager,
        headless_mode: bool = False,
        run_folder: Path | None = None,
        remember_credentials: bool = False,
        developer_mode_enabled: bool = False,
        developer_override_to: str = "",
        developer_override_cc: str = "",
        manual_override_to: str = "",
        manual_override_cc: str = "",
        header_fill_color: str = "",
        source_mode: str = "outlook",
        irt_court_scope: str = "both",
        irt_start_date: date | None = None,
        irt_end_date: date | None = None,
    ):
        super().__init__()
        self.user_id = user_id
        self.password = password
        self.excel_path = excel_path
        self.logger = logger
        self.file_manager = file_manager
        self.headless_mode = headless_mode
        self.run_folder = run_folder
        self.remember_credentials = remember_credentials
        self.developer_mode_enabled = developer_mode_enabled
        self.developer_override_to = developer_override_to
        self.developer_override_cc = developer_override_cc
        self.manual_override_to = manual_override_to
        self.manual_override_cc = manual_override_cc
        self.header_fill_color = header_fill_color
        self.source_mode = source_mode
        self.irt_court_scope = irt_court_scope
        self.irt_start_date = irt_start_date
        self.irt_end_date = irt_end_date
        self.outlook_import_summary = None
        self._stop_requested = False

    def request_stop(self) -> None:
        """Request a cooperative stop for the active run."""
        if self._stop_requested:
            return
        self._stop_requested = True
        self.requestInterruption()
        if self.logger:
            self.logger.log("Stop requested by user")

    def _is_stop_requested(self) -> bool:
        """Return True when the user has asked to stop the run."""
        return self._stop_requested or self.isInterruptionRequested()

    def _raise_if_stop_requested(self) -> None:
        """Abort promptly when a stop request is active."""
        if self._is_stop_requested():
            raise InterruptedError("Run stopped by user.")

    def _finalize_cancellation(self, excel_handler=None) -> None:
        """Save partial work when possible and emit a calm cancelled state."""
        output_path = None
        message = "Run stopped by user."

        if excel_handler is not None and self.excel_path:
            try:
                self.logger.log(
                    f"Attempting to save partial Excel file after stop request: {self.excel_path}"
                )
                excel_handler.save(self.excel_path)
                self.logger.log(
                    f"Successfully saved partial Excel file: {self.excel_path}"
                )
                output_path = self.excel_path
                message += " Partial progress was saved."
            except Exception as e:
                if self.logger:
                    self.logger.log(
                        f"Could not save partial Excel file after stop request: {e}"
                    )
                message += " Partial progress could not be saved automatically."

        if self.logger:
            try:
                self.logger.log("Saving log file")
                self.logger.save_log()
            except Exception:
                pass

        self.cancelled.emit(message, output_path)

    def _search_current_lni(self, scraper, lni: str, is_first_search: bool) -> tuple[bool, bool]:
        """Start or restart the search flow for the current LNI."""
        self._raise_if_stop_requested()

        if is_first_search:
            search_ok = scraper.search_lni(lni, is_first_search=True)
            return search_ok, False

        if scraper.click_new_search():
            return scraper.search_lni(lni, is_first_search=False), False

        self.logger.log(
            "New Search button was not available; attempting to reuse the current search field."
        )
        return scraper.search_lni(lni, is_first_search=False), False

    def _extract_lni_with_retry(
        self,
        scraper,
        lni: str,
        is_first_search: bool,
        processed: int,
        total_lnis: int,
        progress: int,
    ) -> tuple[str, bool]:
        """Extract one LNI, retrying once when the result stage looks transient."""
        last_failure_detail = "result card did not appear"

        for attempt in range(1, self.MAX_LNI_ATTEMPTS + 1):
            self._raise_if_stop_requested()

            if attempt > 1:
                retry_status = (
                    f"Retrying LNI {processed}/{total_lnis} after temporary result-load issue..."
                )
                self.progress_update.emit(progress, retry_status)
                self.logger.log(
                    f"Retrying LNI {lni} (attempt {attempt}/{self.MAX_LNI_ATTEMPTS}) "
                    f"because {last_failure_detail}."
                )

            search_ok, is_first_search = self._search_current_lni(
                scraper,
                lni,
                is_first_search,
            )
            if not search_ok:
                last_failure_detail = "the search/results panel did not finish loading"
                continue

            if not scraper.click_administrative_materials():
                last_failure_detail = "Administrative Materials could not be opened"
                continue

            found, result_element = scraper.find_result_card()
            if not found:
                last_failure_detail = "the result card did not appear in time"
                continue

            if not scraper.click_result_card(result_element):
                last_failure_detail = "the result card could not be opened"
                continue

            lexis_cite = scraper.extract_lexis_cite()
            if not lexis_cite or lexis_cite == "Not Available":
                last_failure_detail = "the Lexis Cite did not finish loading"
                continue

            if attempt > 1:
                self.logger.log(f"Retry succeeded for LNI {lni}")
            return lexis_cite, is_first_search

        self.logger.log(
            f"LNI {lni} remained unavailable after {self.MAX_LNI_ATTEMPTS} attempt(s): "
            f"{last_failure_detail}"
        )
        return "Not Available", is_first_search

    def run(self):
        """Run the extraction process."""
        scraper = None
        excel_handler = None

        try:
            from utils.excel_handler import ExcelHandler
            from utils.irt_intake import IRTQuery, import_irt_results_to_workbook
            from utils.outlook_intake import (
                find_conversion_email_context,
                import_conversion_email_to_workbook,
            )
            from automation.lexis_scraper import LexisScraper

            recipient_override_to = self.manual_override_to or (
                self.developer_override_to if self.developer_mode_enabled else ""
            )
            recipient_override_cc = (
                self.manual_override_cc
                if self.manual_override_to
                else (self.developer_override_cc if self.developer_mode_enabled else "")
            )

            if self.excel_path is None:
                try:
                    self._raise_if_stop_requested()
                    if self.run_folder is None:
                        self.run_folder = self.file_manager.create_run_folder()

                    if self.source_mode == "irt":
                        if self.irt_start_date is None or self.irt_end_date is None:
                            raise RuntimeError("IRT date filters were not provided.")

                        self.progress_update.emit(0, "Importing source data from IRT...")
                        self.logger.log("Importing source data from IRT Search Inventory")
                        import_summary = import_irt_results_to_workbook(
                            run_folder=self.run_folder,
                            query=IRTQuery(
                                court_scope=self.irt_court_scope,
                                start_date=self.irt_start_date,
                                end_date=self.irt_end_date,
                            ),
                            logger=self.logger,
                            header_fill_color=self.header_fill_color,
                            headless_mode=self.headless_mode,
                            cancel_check=self._is_stop_requested,
                        )
                        self.excel_path = import_summary.workbook_path
                        self.progress_update.emit(
                            0,
                            f"Imported {import_summary.imported_row_count} row(s) from IRT results",
                        )
                        self.logger.log(
                            f"Source workbook created from IRT results: {self.excel_path}"
                        )
                        if import_summary.selected_headers:
                            self.logger.log(
                                "Selected IRT result headers: "
                                + ", ".join(import_summary.selected_headers)
                            )
                    else:
                        self.progress_update.emit(0, "Importing source data from Outlook...")
                        self.logger.log("Importing source data from Outlook email")
                        import_summary = import_conversion_email_to_workbook(
                            run_folder=self.run_folder,
                            logger=self.logger,
                            header_fill_color=self.header_fill_color,
                            cancel_check=self._is_stop_requested,
                        )
                        self.outlook_import_summary = import_summary
                        self.excel_path = import_summary.workbook_path
                        self.progress_update.emit(
                            0,
                            f"Imported {import_summary.imported_row_count} row(s) from Outlook email",
                        )
                        self.logger.log(
                            f"Source workbook created from Outlook email: {self.excel_path}"
                        )
                except InterruptedError:
                    raise
                except Exception as e:
                    error_msg = (
                        f"Error importing source data from {self.source_mode}: {e}\n"
                        f"{traceback.format_exc()}"
                    )
                    self.logger.log(error_msg)
                    self.finished.emit(
                        False,
                        f"Failed to import source data from {self.source_mode.title()}: {e}",
                        None,
                    )
                    return

            if self.outlook_import_summary is None and not recipient_override_to:
                try:
                    self._raise_if_stop_requested()
                    source_label = (
                        "manual workbook source"
                        if self.source_mode == "manual"
                        else "IRT workbook source"
                    )
                    self.logger.log(f"Resolving Outlook reply context for {source_label}")
                    self.outlook_import_summary = find_conversion_email_context(
                        logger=self.logger,
                        open_inbox=False,
                        cancel_check=self._is_stop_requested,
                    )
                except InterruptedError:
                    raise
                except Exception as e:
                    self.logger.log(
                        "Could not resolve Outlook reply context for the "
                        f"{source_label}: {e}"
                    )

            try:
                self._raise_if_stop_requested()
                scraper = LexisScraper(
                    self.logger,
                    cancel_check=self._is_stop_requested,
                )
                excel_handler = ExcelHandler(
                    header_fill_color=self.header_fill_color
                )
                self.logger.log(f"Opening Excel file: {self.excel_path}")
                excel_handler.open_excel_file(self.excel_path)
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error initializing components: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.finished.emit(False, f"Failed to initialize: {e}", None)
                return

            try:
                self._raise_if_stop_requested()
                lni_data = excel_handler.read_lni_data()
                total_lnis = len(lni_data)
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error reading LNI data: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.finished.emit(False, f"Failed to read Excel file: {e}", None)
                return

            if total_lnis == 0:
                self.logger.log("No LNI data found in Excel file")
                self.finished.emit(False, "No LNI data found in Excel file", None)
                return

            self.progress_update.emit(0, f"Starting extraction of {total_lnis} LNIs...")
            self.logger.log(f"Starting extraction of {total_lnis} LNIs")

            try:
                self._raise_if_stop_requested()
                if not scraper.launch_browser(headless_mode=self.headless_mode):
                    self.logger.log("Failed to launch browser")
                    self.finished.emit(False, "Failed to launch browser", None)
                    return

                if not scraper.navigate_to_lexis():
                    self.logger.log("Failed to navigate to Lexis website")
                    self.finished.emit(False, "Failed to navigate to Lexis website", None)
                    return

                login_result = scraper.login(self.user_id, self.password)
                if not login_result.success:
                    self.logger.log(
                        "Login failed; credentials were not saved | "
                        f"reason={login_result.reason} | detail={login_result.message}"
                    )

                    if login_result.reason == scraper.LOGIN_REASON_INVALID_CREDENTIALS:
                        user_message = (
                            "Login failed because Lexis rejected the ID or password. "
                            "Please verify your credentials and retry."
                        )
                    elif login_result.reason == scraper.LOGIN_REASON_NETWORK_OR_SITE:
                        user_message = (
                            "Login could not complete because the Lexis site or network "
                            "appears slow/unavailable. Please retry shortly."
                        )
                    elif login_result.reason == scraper.LOGIN_REASON_AUTH_CHALLENGE:
                        user_message = (
                            "Login was interrupted by an additional verification challenge "
                            "(MFA/captcha/security check). Please complete the challenge "
                            "manually and retry."
                        )
                    else:
                        user_message = (
                            "Login did not complete. This may be due to credentials, "
                            "network latency, or a site-side issue. Please retry and "
                            "check the run log for details."
                        )

                    self.finished.emit(False, user_message, None)
                    return

                if self.remember_credentials:
                    save_credentials(self.user_id, self.password)
                    self.logger.log("Credentials saved securely using the OS keyring")
                else:
                    clear_credentials()
                    self.logger.log("Saved credentials cleared")

            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error during browser setup/login: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.finished.emit(False, f"Browser/login error: {e}", None)
                return

            processed = 0
            is_first_search = True

            for row, lni in lni_data:
                try:
                    self._raise_if_stop_requested()
                    processed += 1
                    progress = int((processed / total_lnis) * 100)
                    self.progress_update.emit(
                        progress,
                        f"Processing LNI {processed}/{total_lnis}: {lni}",
                    )

                    lexis_cite, is_first_search = self._extract_lni_with_retry(
                        scraper=scraper,
                        lni=lni,
                        is_first_search=is_first_search,
                        processed=processed,
                        total_lnis=total_lnis,
                        progress=progress,
                    )
                    excel_handler.write_lexis_cite(row, lexis_cite)

                    if lexis_cite == "Not Available":
                        self.progress_update.emit(
                            progress,
                            f"No results found for LNI {processed}/{total_lnis}",
                        )
                    else:
                        self.progress_update.emit(
                            progress,
                            f"Successfully extracted LNI {processed}/{total_lnis}",
                        )

                except InterruptedError:
                    raise
                except Exception as e:
                    error_msg = (
                        f"Error processing LNI {lni} (Row {row}): "
                        f"{e}\n{traceback.format_exc()}"
                    )
                    self.logger.log(error_msg)
                    try:
                        excel_handler.write_lexis_cite(row, "Not Available")
                    except Exception as write_error:
                        self.logger.log(
                            f"Failed to write 'Not Available' for row {row}: {write_error}"
                        )
                    continue

            try:
                self._raise_if_stop_requested()
                self.logger.log(f"Attempting to save updated Excel file: {self.excel_path}")
                excel_handler.save(self.excel_path)
                self.logger.log(f"Successfully saved updated Excel file: {self.excel_path}")
                output_path = self.excel_path
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error saving output file: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                self.logger.save_log()
                self.finished.emit(False, f"Failed to save output file: {e}", None)
                return

            completion_message = "Extraction completed successfully."

            try:
                self._raise_if_stop_requested()
                from utils.outlook_mailer import (
                    NoSuccessfulExtractionsError,
                    send_extraction_email,
                )

                if self.outlook_import_summary is not None:
                    email_summary = send_extraction_email(
                        output_path,
                        self.logger,
                        source_message_entry_id=self.outlook_import_summary.message_entry_id,
                        source_message_store_id=self.outlook_import_summary.message_store_id,
                        fallback_to=self.outlook_import_summary.to_recipients,
                        fallback_cc=self.outlook_import_summary.cc_recipients,
                        override_to=recipient_override_to or None,
                        override_cc=recipient_override_cc or None,
                    )
                else:
                    email_summary = send_extraction_email(
                        output_path,
                        self.logger,
                        override_to=recipient_override_to or None,
                        override_cc=recipient_override_cc or None,
                    )

                completion_message = (
                    "Extraction completed successfully. "
                    f"Outlook email sent with {email_summary.success_count} available "
                    f"{'document' if email_summary.success_count == 1 else 'documents'}."
                )
            except NoSuccessfulExtractionsError as e:
                self.logger.log(str(e))
                completion_message = str(e)
            except InterruptedError:
                raise
            except Exception as e:
                error_msg = f"Error sending Outlook email: {e}\n{traceback.format_exc()}"
                self.logger.log(error_msg)
                completion_message = (
                    "Extraction completed successfully, but the Outlook email could not "
                    f"be sent automatically: {e}"
                )

            try:
                self.logger.log("Saving log file")
                self.logger.save_log()
            except Exception as e:
                self.logger.log(f"Error saving log file: {e}\n{traceback.format_exc()}")

            self.finished.emit(
                True,
                completion_message,
                output_path,
            )

        except InterruptedError:
            self._finalize_cancellation(excel_handler=excel_handler)

        except Exception as e:
            error_msg = f"Critical error in extraction process: {e}\n{traceback.format_exc()}"
            if self.logger:
                self.logger.log(error_msg)
                try:
                    self.logger.save_log()
                except Exception:
                    pass
            self.finished.emit(False, f"Extraction failed: {e}", None)

        finally:
            if scraper is not None:
                try:
                    scraper.close_browser()
                    self.logger.log("Browser closed successfully")
                except Exception as e:
                    self.logger.log(f"Error closing browser: {e}")


class DraggableTitleBar(QFrame):
    """A simple draggable title bar for a frameless window."""

    def mousePressEvent(self, event):
        """Store the drag offset when the user starts dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if isinstance(window, MainWindow):
                window.drag_offset = (
                    event.globalPosition().toPoint() - window.frameGeometry().topLeft()
                )
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Move the window while dragging."""
        if event.buttons() & Qt.MouseButton.LeftButton:
            window = self.window()
            if isinstance(window, MainWindow) and window.drag_offset is not None:
                window.move(event.globalPosition().toPoint() - window.drag_offset)
                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Clear the stored drag offset when dragging ends."""
        window = self.window()
        if isinstance(window, MainWindow):
            window.drag_offset = None

        super().mouseReleaseEvent(event)


class FolderTabButton(QPushButton):
    """Small folder-style tab button with a double-click signal."""

    double_clicked = pyqtSignal()

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("RecipientFolderTab")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, event):
        """Emit a dedicated signal so the popup can expand the editor."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class RecipientOverridePopup(QFrame):
    """Compact popup for manual recipient overrides."""

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RecipientPanel")
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())

        self.expanded = False
        self.leave_timer = QTimer(self)
        self.leave_timer.setSingleShot(True)
        self.leave_timer.timeout.connect(self._hide_if_cursor_left)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(12, 12, 12, 12)

        self.shell = QFrame()
        self.shell.setObjectName("RecipientPopupShell")
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setSpacing(0)
        shell_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_strip = QWidget()
        self.tab_strip.setObjectName("RecipientTabStrip")
        tab_layout = QHBoxLayout(self.tab_strip)
        tab_layout.setSpacing(6)
        tab_layout.setContentsMargins(12, 0, 12, 0)

        self.to_tab_button = FolderTabButton("To")
        self.to_tab_button.clicked.connect(lambda _checked=False: self._on_tab_clicked(0))
        self.to_tab_button.double_clicked.connect(lambda: self._on_tab_double_clicked(0))
        tab_layout.addWidget(self.to_tab_button)

        self.cc_tab_button = FolderTabButton("CC")
        self.cc_tab_button.clicked.connect(lambda _checked=False: self._on_tab_clicked(1))
        self.cc_tab_button.double_clicked.connect(lambda: self._on_tab_double_clicked(1))
        tab_layout.addWidget(self.cc_tab_button)
        tab_layout.addStretch(1)
        shell_layout.addWidget(self.tab_strip)

        self.panel = QFrame()
        self.panel.setObjectName("RecipientTabPanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setSpacing(0)
        panel_layout.setContentsMargins(14, 14, 14, 14)

        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("RecipientTabStack")

        self.to_input = QPlainTextEdit()
        self.to_input.setPlaceholderText("name@example.com; team@example.com")
        self._configure_editor(self.to_input)
        self.editor_stack.addWidget(self._wrap_editor(self.to_input))

        self.cc_input = QPlainTextEdit()
        self.cc_input.setPlaceholderText("observer@example.com")
        self._configure_editor(self.cc_input)
        self.editor_stack.addWidget(self._wrap_editor(self.cc_input))

        panel_layout.addWidget(self.editor_stack)
        shell_layout.addWidget(self.panel)
        layout.addWidget(self.shell)

        self.tab_bridge = QFrame(self.shell)
        self.tab_bridge.setObjectName("RecipientTabBridge")
        self.tab_bridge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.tab_bridge.hide()

        self.tab_buttons = (self.to_tab_button, self.cc_tab_button)

        self.setMinimumWidth(424)
        self.resize(436, 132)
        self._set_active_tab(0)
        self._set_expanded(False)

    def _wrap_editor(self, editor: QPlainTextEdit) -> QWidget:
        """Wrap the editor so each tab page keeps clean, even padding."""
        page = QWidget()
        page.setObjectName("RecipientTabPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        page_layout.addWidget(editor)
        return page

    def _configure_editor(self, editor: QPlainTextEdit) -> None:
        """Apply compact popup-editor defaults."""
        editor.setMinimumHeight(38)
        editor.setMaximumHeight(38)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setTabChangesFocus(True)

    def _active_editor(self) -> QPlainTextEdit:
        """Return the editor for the currently selected recipient tab."""
        return self.to_input if self.editor_stack.currentIndex() == 0 else self.cc_input

    def _set_active_tab(self, index: int) -> None:
        """Switch the visible editor page and update the folder tab styling."""
        self.editor_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.tab_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.blockSignals(False)
        self._reposition_tab_bridge()
        self._focus_active_editor()

    def _set_expanded(self, expanded: bool) -> None:
        """Toggle compact vs expanded editor height inside the popup."""
        self.expanded = expanded
        editor_height = 132 if expanded else 46
        popup_height = 226 if expanded else 132

        for editor in (self.to_input, self.cc_input):
            editor.setMinimumHeight(editor_height)
            editor.setMaximumHeight(editor_height)
            editor.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
                if expanded
                else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

        self.resize(self.width(), popup_height)
        self._reposition_tab_bridge()

    def _on_tab_clicked(self, index: int) -> None:
        """Switch tabs without changing the current compact/expanded state."""
        self._set_active_tab(index)

    def _on_tab_double_clicked(self, index: int) -> None:
        """Expand or collapse the active tab editor on tab double-click."""
        same_tab = index == self.editor_stack.currentIndex()
        self._set_active_tab(index)
        self._set_expanded(not self.expanded if same_tab else True)

    def _focus_active_editor(self) -> None:
        """Move focus into the active tab's editor when tabs change."""
        self._active_editor().setFocus()

    def _reposition_tab_bridge(self) -> None:
        """Bridge the active tab to the panel so it reads like a real folder tab."""
        if not self.isVisible():
            return

        active_button = next((button for button in self.tab_buttons if button.isChecked()), None)
        if active_button is None:
            self.tab_bridge.hide()
            return

        button_top_left = active_button.mapTo(self.shell, QPoint(0, 0))
        panel_top_left = self.panel.mapTo(self.shell, QPoint(0, 0))
        bridge_width = max(32, active_button.width() - 8)
        bridge_height = 9
        bridge_x = button_top_left.x() + 4
        bridge_y = panel_top_left.y() - 4
        self.tab_bridge.setGeometry(bridge_x, bridge_y, bridge_width, bridge_height)
        self.tab_bridge.show()
        self.tab_bridge.raise_()

    def resizeEvent(self, event):
        """Keep the active-tab bridge aligned during popup resizes."""
        super().resizeEvent(event)
        self._reposition_tab_bridge()

    def show_for_button(self, button: QWidget) -> None:
        """Show the popup aligned beneath the anchor button."""
        self._set_expanded(False)
        self._set_active_tab(0)
        button_bottom_left = button.mapToGlobal(QPoint(0, button.height() + 8))
        self.move(button_bottom_left)
        self.show()
        self.raise_()
        self.to_input.setFocus()
        self.to_input.selectAll()
        self._reposition_tab_bridge()
        if not self.frameGeometry().contains(QCursor.pos()):
            self.leave_timer.start(650)

    def enterEvent(self, event):
        """Keep the popup open while the cursor is inside it."""
        self.leave_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Close shortly after the cursor leaves the popup frame."""
        self.leave_timer.start(180)
        super().leaveEvent(event)

    def focusOutEvent(self, event):
        """Close when focus leaves the popup and the cursor is elsewhere."""
        self.leave_timer.start(60)
        super().focusOutEvent(event)

    def hideEvent(self, event):
        """Notify the parent when the popup closes."""
        self.leave_timer.stop()
        self._set_expanded(False)
        self.tab_bridge.hide()
        self.closed.emit()
        super().hideEvent(event)

    def _hide_if_cursor_left(self) -> None:
        """Hide the popup when the cursor is no longer inside its frame."""
        if not self.frameGeometry().contains(QCursor.pos()):
            self.hide()


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.extraction_thread = None
        self.drag_offset = None
        self.developer_mode_enabled = False
        self.dev_preview_compact_mode = False
        self.password_visible = False
        self.title_font_family = ""
        self.header_color_options = self._load_header_color_options()
        self.header_fill_color = self._load_saved_header_fill_color()
        self.source_mode_options = (
            ("Outlook Email", "outlook"),
            ("IRT Results", "irt"),
            ("Manual Template", "manual"),
        )
        self.developer_mode_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.developer_mode_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.developer_mode_shortcut.setAutoRepeat(False)
        self.developer_mode_shortcut.activated.connect(self._toggle_developer_mode)
        self._load_custom_fonts()
        self.init_ui()
        self.load_saved_credentials()

    def _apply_window_theme(self):
        """Apply the application stylesheet."""
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #090d16;
            }

            QWidget#AppSurface {
                background-color: #090d16;
            }

            QFrame#HeaderCard {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #101826,
                    stop: 1 #172033
                );
                border: 1px solid #22304a;
                border-radius: 18px;
            }

            QFrame#SectionCard {
                background-color: #101722;
                border: 1px solid #1d293d;
                border-radius: 16px;
            }

            QLabel#EyebrowLabel {
                color: #67e8f9;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.08em;
                font-family: "Segoe UI Semibold";
            }

            QLabel#HeroTitle {
                color: #f8fafc;
                font-size: 30px;
                font-weight: 700;
                letter-spacing: 0.03em;
            }

            QLabel#SectionTitle {
                color: #e2e8f0;
                font-size: 13px;
                font-weight: 700;
            }

            QLabel#SectionDescription {
                color: #8aa0bd;
                font-size: 11px;
            }

            QLabel#FieldLabel {
                color: #93a8c3;
                font-size: 11px;
                font-weight: 600;
            }

            QLineEdit {
                background-color: #0b1220;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 12px;
                selection-background-color: #22d3ee;
            }

            QLineEdit:focus {
                background-color: #0d1525;
                border: 1px solid #22d3ee;
            }

            QPlainTextEdit {
                background-color: #0b1220;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 12px;
                selection-background-color: #22d3ee;
            }

            QPlainTextEdit:focus {
                background-color: #0d1525;
                border: 1px solid #22d3ee;
            }

            QComboBox,
            QDateEdit {
                background-color: #0b1220;
                color: #f8fafc;
                border: 1px solid #22304a;
                border-radius: 12px;
                padding: 8px 40px 8px 12px;
                font-size: 12px;
                min-height: 20px;
                combobox-popup: 1;
            }

            QComboBox:hover,
            QDateEdit:hover {
                background-color: #0d1525;
            }

            QComboBox:focus,
            QDateEdit:focus {
                border: 1px solid #22d3ee;
            }

            QComboBox:on {
                background-color: #0f1827;
                border: 1px solid #22d3ee;
            }

            QComboBox::drop-down,
            QDateEdit::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 32px;
                border-left: 1px solid #22304a;
                border-top-right-radius: 12px;
                border-bottom-right-radius: 12px;
                background-color: #0f1827;
            }

            QComboBox::down-arrow,
            QDateEdit::down-arrow {
                image: url(gui/chevron_down.svg);
                width: 12px;
                height: 8px;
            }

            QComboBox QAbstractItemView {
                background-color: #0f1827;
                color: #f8fafc;
                border: 1px solid #2a3d5a;
                border-radius: 14px;
                padding: 6px;
                selection-background-color: #16304a;
                selection-color: #f8fafc;
                outline: 0;
            }

            QComboBox QAbstractItemView::item {
                min-height: 30px;
                margin: 2px 4px;
                padding: 0 10px;
                border-radius: 9px;
            }

            QComboBox QAbstractItemView::item:hover {
                background-color: #122135;
            }

            QComboBox QAbstractItemView::item:selected {
                background-color: #16304a;
            }

            QCheckBox {
                color: #cbd5e1;
                font-size: 11px;
                spacing: 8px;
            }

            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 5px;
                border: 1px solid #334155;
                background-color: #0b1220;
            }

            QCheckBox::indicator:checked {
                background-color: #22d3ee;
                border: 1px solid #22d3ee;
                image: url(gui/checkmark.svg);
            }

            QPushButton {
                min-height: 40px;
                border-radius: 12px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 600;
            }

            QPushButton#PrimaryButton {
                background-color: #22d3ee;
                color: #03131a;
                border: 1px solid #22d3ee;
            }

            QPushButton#PrimaryButton:hover {
                background-color: #67e8f9;
                border-color: #67e8f9;
            }

            QPushButton#SecondaryButton {
                background-color: #131c2b;
                color: #dbe7f5;
                border: 1px solid #243248;
            }

            QPushButton#SecondaryButton:hover {
                background-color: #182335;
            }

            QPushButton#GhostButton {
                background-color: #101722;
                color: #9fb2ca;
                border: 1px solid #22304a;
            }

            QPushButton#GhostButton:hover {
                background-color: #131c2b;
            }

            QPushButton#StopButton {
                min-height: 36px;
                max-width: 172px;
                background-color: #221216;
                color: #fecdd3;
                border: 1px solid #7f1d1d;
            }

            QPushButton#StopButton:hover {
                background-color: #2f151b;
                color: #ffe4e6;
                border-color: #991b1b;
            }

            QPushButton#PrimaryButton:disabled,
            QPushButton#SecondaryButton:disabled,
            QPushButton#GhostButton:disabled,
            QPushButton#StopButton:disabled {
                background-color: #16202f;
                color: #5e7088;
                border-color: #16202f;
            }

            QPushButton#TitleBarButton,
            QPushButton#CloseTitleBarButton {
                min-height: 28px;
                max-height: 28px;
                min-width: 28px;
                max-width: 28px;
                border-radius: 8px;
                background-color: transparent;
                color: #9fb2ca;
                border: 1px solid transparent;
                padding: 0;
                font-size: 13px;
                font-weight: 700;
            }

            QPushButton#CloseTitleBarButton {
                font-size: 17px;
                font-weight: 800;
            }

            QPushButton#TitleBarButton:hover {
                background-color: #182335;
                color: #f8fafc;
                border-color: #243248;
            }

            QPushButton#CloseTitleBarButton:hover {
                background-color: #7f1d1d;
                color: #ffffff;
                border-color: #991b1b;
            }

            QProgressBar {
                min-height: 12px;
                border-radius: 6px;
                background-color: #0b1220;
                border: none;
                text-align: center;
                color: #e2e8f0;
                font-size: 10px;
                font-weight: 700;
            }

            QProgressBar::chunk {
                border-radius: 6px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #22d3ee,
                    stop: 1 #2563eb
                );
            }

            QLabel#ProgressLabel {
                color: #e2e8f0;
                font-size: 12px;
                font-weight: 500;
            }

            QLabel#ProgressMeta {
                color: #7f93ae;
                font-size: 11px;
            }

            QLabel#DeveloperPill {
                background-color: #2b1f0f;
                color: #fbbf24;
                border: 1px solid #5b4417;
                border-radius: 9px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 700;
            }

            QFrame#DeveloperPanel {
                background-color: #0b1220;
                border: 1px solid #22304a;
                border-radius: 12px;
            }

            QFrame#InlineFiltersCard {
                background-color: #0c1422;
                border: 1px solid #22304a;
                border-radius: 12px;
            }

            QPushButton#InlineToggleButton {
                min-height: 34px;
                max-height: 34px;
                border-radius: 11px;
                padding: 0 14px;
                font-size: 11px;
                font-weight: 600;
                background-color: #101722;
                color: #cbd5e1;
                border: 1px solid #243248;
            }

            QPushButton#InlineToggleButton:hover {
                background-color: #131c2b;
                color: #f8fafc;
            }

            QPushButton#InlineToggleButton:checked {
                background-color: #112638;
                color: #67e8f9;
                border-color: #1f6f8b;
            }

            QFrame#RecipientPanel,
            QFrame#RecipientPopupShell,
            QWidget#RecipientTabStrip {
                background: transparent;
                border: none;
            }

            QFrame#RecipientTabPanel {
                background-color: #0a1220;
                border: 1px solid #22304a;
                border-radius: 14px;
            }

            QFrame#RecipientTabBridge {
                background-color: #0a1220;
                border: none;
            }

            QPushButton#RecipientFolderTab {
                background-color: #0f1827;
                color: #8fa6c4;
                border: 1px solid #22304a;
                border-top-left-radius: 11px;
                border-top-right-radius: 11px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                padding: 0 18px;
                min-width: 60px;
                min-height: 32px;
                max-height: 32px;
                font-size: 11px;
                font-weight: 600;
                margin-top: 4px;
            }

            QPushButton#RecipientFolderTab:checked {
                background-color: #0a1220;
                color: #f8fafc;
                border-color: #314662;
                margin-top: 0px;
                padding-bottom: 3px;
                border-bottom-color: #0a1220;
            }

            QPushButton#RecipientFolderTab:hover:!checked {
                background-color: #142032;
                color: #dbe7f5;
            }
            """
        )

    def _load_custom_fonts(self):
        """Load bundled fonts and cache the title font family."""
        font_path = Path(__file__).resolve().parent.parent / "assets" / "Baron_Neue_Black.otf"
        if not font_path.exists():
            return

        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            return

        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            self.title_font_family = families[0]

    def _load_header_color_options(self) -> list[tuple[str, str]]:
        """Load the available workbook header color presets."""
        from utils.excel_handler import ExcelHandler

        return list(ExcelHandler.HEADER_COLOR_PRESETS)

    def _load_saved_header_fill_color(self) -> str:
        """Load and normalize the persisted workbook header color."""
        from utils.excel_handler import ExcelHandler

        saved_color = load_setting(
            HEADER_FILL_COLOR_KEY,
            ExcelHandler.DEFAULT_HEADER_FILL_COLOR,
        )
        return ExcelHandler.normalize_header_fill_color(saved_color)

    def _default_irt_date_range(self) -> tuple[date, date]:
        """Return the current week's Thursday-to-Saturday default IRT window."""
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        return week_start + timedelta(days=3), week_start + timedelta(days=5)

    def _apply_default_irt_filters(self) -> None:
        """Seed the IRT controls with the current week's standard date range."""
        start_date, end_date = self._default_irt_date_range()
        self.irt_start_date_edit.setDate(QDate(start_date.year, start_date.month, start_date.day))
        self.irt_end_date_edit.setDate(QDate(end_date.year, end_date.month, end_date.day))
        self.irt_start_date_edit.setToolTip(
            f"Default IRT start date: {start_date.strftime('%B %d, %Y')}"
        )
        self.irt_end_date_edit.setToolTip(
            f"Default IRT end date: {end_date.strftime('%B %d, %Y')}"
        )

    def _create_color_swatch_icon(self, color_hex: str) -> QIcon:
        """Build a compact swatch icon for the header-color dropdown."""
        swatch = QPixmap(16, 16)
        swatch.fill(Qt.GlobalColor.transparent)

        painter = QPainter(swatch)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#243248"))
        painter.setBrush(QColor(f"#{color_hex}"))
        painter.drawRoundedRect(1, 1, 14, 14, 4, 4)
        painter.end()

        return QIcon(swatch)

    def _configure_dropdown_combo(self, combo: QComboBox) -> None:
        """Make compact selectors feel like true popup dropdowns."""
        popup_view = QListView(combo)
        popup_view.setSpacing(4)
        popup_view.setUniformItemSizes(True)
        popup_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        popup_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        popup_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        popup_view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setView(popup_view)
        combo.setMaxVisibleItems(8)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)

    def _populate_header_color_combo(self):
        """Fill the dropdown with preset Excel header colors."""
        self.header_color_combo.blockSignals(True)
        self.header_color_combo.clear()

        selected_index = 0
        for index, (label, color_hex) in enumerate(self.header_color_options):
            self.header_color_combo.addItem(
                self._create_color_swatch_icon(color_hex),
                label,
            )
            self.header_color_combo.setItemData(
                index,
                color_hex,
                Qt.ItemDataRole.UserRole,
            )
            self.header_color_combo.setItemData(
                index,
                f"#{color_hex}",
                Qt.ItemDataRole.ToolTipRole,
            )

            if color_hex == self.header_fill_color:
                selected_index = index

        self.header_color_combo.setCurrentIndex(selected_index)
        self.header_color_combo.blockSignals(False)
        self._update_header_color_tooltip()

    def _update_header_color_tooltip(self):
        """Keep the header-color selector tooltip aligned with the current value."""
        current_color = self.header_color_combo.currentData(Qt.ItemDataRole.UserRole)
        if current_color:
            self.header_color_combo.setToolTip(
                f"Workbook and Outlook table header color: #{current_color}"
            )

    def _on_header_color_changed(self, *_args):
        """Persist the selected workbook header color for future runs."""
        selected_color = self.header_color_combo.currentData(Qt.ItemDataRole.UserRole)
        if not selected_color:
            return

        self.header_fill_color = str(selected_color)
        save_setting(HEADER_FILL_COLOR_KEY, self.header_fill_color)
        self._update_header_color_tooltip()

    def _populate_source_mode_combo(self):
        """Fill the source selector with supported extraction source modes."""
        self.source_mode_combo.blockSignals(True)
        self.source_mode_combo.clear()

        for label, source_mode in self.source_mode_options:
            self.source_mode_combo.addItem(label)
            index = self.source_mode_combo.count() - 1
            self.source_mode_combo.setItemData(
                index,
                source_mode,
                Qt.ItemDataRole.UserRole,
            )

        self.source_mode_combo.setCurrentIndex(0)
        self.source_mode_combo.blockSignals(False)
        self._update_source_mode_tooltip()

    def _current_source_mode(self) -> str:
        """Return the currently selected extraction source mode."""
        if not hasattr(self, "source_mode_combo"):
            return "outlook"
        selected_mode = self.source_mode_combo.currentData(Qt.ItemDataRole.UserRole)
        return str(selected_mode or "outlook")

    def _update_source_mode_tooltip(self):
        """Describe the behavior of the currently selected source mode."""
        source_mode = self._current_source_mode()
        if source_mode == "manual":
            self.source_mode_combo.setToolTip(
                "Run Extraction will use the most recently edited Excel workbook in the Results folder."
            )
            return
        if source_mode == "irt":
            self.source_mode_combo.setToolTip(
                "Run Extraction will import rows from IRT Search Inventory using the selected court/date filters."
            )
            return

        self.source_mode_combo.setToolTip(
            "Run Extraction will import the source table from the latest matching Outlook email."
        )

    def _idle_ready_detail(self) -> str:
        """Return source-aware helper copy for the idle state."""
        source_mode = self._current_source_mode()
        if source_mode == "manual":
            return "Enter your credentials, then run extraction using the latest manual workbook."
        if source_mode == "irt":
            return (
                "Enter your credentials, then import IRT Search Inventory results and "
                "continue with extraction."
            )
        return "Enter your credentials, then run the automated Outlook-to-Lexis workflow."

    def _on_source_mode_changed(self, *_args):
        """Refresh helper copy when the extraction source mode changes."""
        self._update_source_mode_tooltip()
        self.irt_filters_frame.setVisible(self._current_source_mode() == "irt")
        self._update_window_size()
        if self.extraction_thread is None and not self.developer_mode_enabled:
            self._set_status_state("Ready", self._idle_ready_detail(), "ready")

    def _update_window_size(self):
        """Resize the fixed window based on the active UI mode."""
        base_width, base_height = (
            self.dev_mode_window_size if self.developer_mode_enabled else self.base_window_size
        )
        extra_width = 44 if self._current_source_mode() == "irt" else 0
        extra_height = 72 if self._current_source_mode() == "irt" else 0
        self.setFixedSize(base_width + extra_width, base_height + extra_height)

    def _toggle_recipient_panel(self, checked: bool):
        """Show or hide the manual recipient override popup."""
        if checked:
            self.recipient_popup.show_for_button(self.recipients_toggle_btn)
            return

        self.recipient_popup.hide()

    def _on_recipient_popup_closed(self):
        """Sync the toggle button when the popup closes itself."""
        self.recipients_toggle_btn.blockSignals(True)
        self.recipients_toggle_btn.setChecked(False)
        self.recipients_toggle_btn.blockSignals(False)

    def _apply_card_shadow(self, widget: QFrame):
        """Apply a soft drop shadow to a card."""
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 72))
        widget.setGraphicsEffect(shadow)

    def _create_card(self, object_name: str = "SectionCard") -> QFrame:
        """Create a styled card frame."""
        card = QFrame()
        card.setObjectName(object_name)
        self._apply_card_shadow(card)
        return card

    def _set_status_state(self, state: str, detail: str, tone: str = "ready"):
        """Update the runtime status badge and detail text."""
        palette = {
            "ready": ("#131c2b", "#cbd5e1", "#243248"),
            "working": ("#0f2330", "#67e8f9", "#164e63"),
            "success": ("#0d2018", "#86efac", "#14532d"),
            "error": ("#2a1215", "#fda4af", "#7f1d1d"),
            "warning": ("#2b1f0f", "#fbbf24", "#5b4417"),
        }
        background, foreground, border = palette.get(tone, palette["ready"])
        self.status_badge.setText(state.upper())
        self.status_badge.setStyleSheet(
            f"""
            background-color: {background};
            color: {foreground};
            border: 1px solid {border};
            border-radius: 11px;
            padding: 5px 10px;
            font-size: 10px;
            font-weight: 700;
            """
        )
        self.status_label.setText(detail)

    def _set_developer_mode_state(self, enabled: bool):
        """Toggle developer-only UX hints while reusing the Recipients override."""
        self.developer_mode_enabled = enabled
        self.compact_preview_checkbox.setVisible(enabled)
        self.developer_mode_indicator.setVisible(enabled)
        self.eyebrow.setVisible(not enabled)
        self.progress_meta_label.setVisible(enabled)
        self.status_badge.setAlignment(
            Qt.AlignmentFlag.AlignCenter if not enabled else Qt.AlignmentFlag.AlignLeft
        )
        self.status_row_layout.setAlignment(
            self.status_badge,
            Qt.AlignmentFlag.AlignLeft if enabled else Qt.AlignmentFlag.AlignHCenter,
        )
        self.status_row_layout.setStretch(0, 0 if enabled else 1)
        self.status_row_layout.setStretch(1, 1 if enabled else 0)
        self._update_window_size()
        self._set_runtime_panel_mode(show_runtime=self.extraction_thread is not None)
        self.progress_meta_label.setText(
            "Developer mode uses the Recipients override for safe test routing."
            if enabled
            else "Outlook intake runs automatically."
        )
        if not enabled:
            self.recipient_popup.hide()

        self.recipients_toggle_btn.setToolTip(
            (
                "Override the outgoing To and CC recipients for this run. "
                "In Developer Mode, use this to route email to your test inbox."
            )
            if enabled
            else "Manually override the outgoing To and CC recipients for this run."
        )

        if self.extraction_thread is None:
            self._set_status_state(
                "Developer" if enabled else "Ready",
                (
                    "Developer mode enabled. Use Recipients to route email to your test inbox."
                    if enabled
                    else self._idle_ready_detail()
                ),
                "working" if enabled else "ready",
            )

    def _toggle_developer_mode(self):
        """Toggle Developer Mode for safe test routing with the Recipients override."""
        if self.extraction_thread is not None:
            return

        self._set_developer_mode_state(not self.developer_mode_enabled)

    def _toggle_password_visibility(self):
        """Toggle password visibility in the password input."""
        self.password_visible = not self.password_visible
        self.password_input.setEchoMode(
            QLineEdit.EchoMode.Normal if self.password_visible else QLineEdit.EchoMode.Password
        )
        self._update_password_toggle_action()

    def _update_password_toggle_action(self):
        """Update icon and tooltip for the password visibility toggle."""
        icon_base = "pw_hidden" if self.password_visible else "pw_visible"
        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        icon_candidates = (
            assets_dir / f"{icon_base}.svg",
            assets_dir / f"{icon_base}.png",
        )
        icon_path = next((candidate for candidate in icon_candidates if candidate.exists()), None)
        if not icon_path:
            self.password_toggle_action.setIcon(QIcon())
        elif icon_path.suffix.lower() == ".svg":
            # Prefer SVG for crisp rendering at small action-icon sizes.
            self.password_toggle_action.setIcon(QIcon(str(icon_path)))
        else:
            self.password_toggle_action.setIcon(self._load_tinted_icon(icon_path))
        self.password_toggle_action.setToolTip(
            "Hide password" if self.password_visible else "Show password"
        )

    def _load_tinted_icon(self, icon_path: Path) -> QIcon:
        """Load icon and tint it white for dark-theme visibility."""
        base_icon = QIcon(str(icon_path))
        pixmap = base_icon.pixmap(16, 16)
        if pixmap.isNull():
            return base_icon

        tinted = QPixmap(pixmap.size())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor("#f8fafc"))
        painter.end()
        return QIcon(tinted)

    def _set_runtime_panel_mode(self, show_runtime: bool):
        """Show either single-panel or full layout depending on mode."""
        if self.developer_mode_enabled and not self.dev_preview_compact_mode:
            # Keep the original full developer layout unless preview is enabled.
            self.auth_card.setVisible(True)
            self.runtime_card.setVisible(True)
            return

        self.auth_card.setVisible(not show_runtime)
        self.runtime_card.setVisible(show_runtime)

    def _on_compact_preview_toggled(self, checked: bool):
        """Enable/disable compact two-panel preview while in Developer Mode."""
        self.dev_preview_compact_mode = checked
        self._set_runtime_panel_mode(show_runtime=self.extraction_thread is not None)

    def init_ui(self):
        """Initialize the UI."""
        self.base_window_size = (540, 408)
        self.dev_mode_window_size = (540, 676)
        self.setWindowTitle("PLR000-CCA001 Extractor")
        self._update_window_size()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self._apply_window_theme()

        central_widget = QWidget()
        central_widget.setObjectName("AppSurface")
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)
        central_widget.setLayout(main_layout)

        header_card = self._create_card("HeaderCard")
        header_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header_card.setMinimumHeight(108)
        header_card.setMaximumHeight(108)
        header_layout = QVBoxLayout(header_card)
        header_layout.setSpacing(2)
        header_layout.setContentsMargins(16, 10, 16, 10)

        eyebrow = QLabel("SOURCE ACQUISITION AND DATA MANAGEMENT")
        eyebrow.setObjectName("EyebrowLabel")
        self.eyebrow = eyebrow
        title = QLabel("PLR000-CCA001 Extractor")
        title.setObjectName("HeroTitle")
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        if self.title_font_family:
            title_font = QFont(self.title_font_family, 30)
            title_font.setWeight(QFont.Weight.Black)
            title.setFont(title_font)
        title_bar = DraggableTitleBar()
        title_bar.setObjectName("TitleBarSurface")
        title_bar.setStyleSheet("background: transparent; border: none;")
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setSpacing(8)
        title_bar_layout.setContentsMargins(0, 0, 0, 0)

        eyebrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_bar_layout.addWidget(eyebrow, 0, Qt.AlignmentFlag.AlignTop)

        self.developer_mode_indicator = QLabel("DEV MODE")
        self.developer_mode_indicator.setObjectName("DeveloperPill")
        self.developer_mode_indicator.setVisible(False)
        self.developer_mode_indicator.setToolTip("Developer Mode is enabled. Toggle with Ctrl+K.")
        title_bar_layout.addWidget(
            self.developer_mode_indicator,
            0,
            Qt.AlignmentFlag.AlignTop,
        )

        title_bar_layout.addStretch(1)

        self.minimize_btn = QPushButton("—")
        self.minimize_btn.setObjectName("TitleBarButton")
        self.minimize_btn.setToolTip("Minimize")
        self.minimize_btn.clicked.connect(self.showMinimized)
        title_bar_layout.addWidget(
            self.minimize_btn,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("CloseTitleBarButton")
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(self.close)
        title_bar_layout.addWidget(
            self.close_btn,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        header_layout.addWidget(title_bar)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(title)

        main_layout.addWidget(header_card)

        auth_card = self._create_card()
        self.auth_card = auth_card
        auth_layout = QVBoxLayout(auth_card)
        auth_layout.setSpacing(14)
        auth_layout.setContentsMargins(16, 14, 16, 14)

        credentials_grid = QGridLayout()
        credentials_grid.setHorizontalSpacing(10)
        credentials_grid.setVerticalSpacing(6)

        id_label = QLabel("Lexis ID")
        id_label.setObjectName("FieldLabel")
        credentials_grid.addWidget(id_label, 0, 0, 1, 1)

        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("ID")
        self.id_input.setMinimumHeight(38)
        self.id_input.textChanged.connect(self.on_credentials_changed)
        credentials_grid.addWidget(self.id_input, 1, 0, 1, 1)

        password_label = QLabel("Password")
        password_label.setObjectName("FieldLabel")
        credentials_grid.addWidget(password_label, 0, 1, 1, 1)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setMinimumHeight(38)
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.textChanged.connect(self.on_credentials_changed)
        self.password_toggle_action = QAction(self.password_input)
        self._update_password_toggle_action()
        self.password_toggle_action.triggered.connect(self._toggle_password_visibility)
        self.password_input.addAction(
            self.password_toggle_action,
            QLineEdit.ActionPosition.TrailingPosition,
        )
        credentials_grid.addWidget(self.password_input, 1, 1, 1, 1)

        auth_layout.addLayout(credentials_grid)
        auth_layout.addSpacing(12)

        toggles_layout = QHBoxLayout()
        toggles_layout.setSpacing(18)

        self.save_credentials_checkbox = QCheckBox("Remember ID")
        toggles_layout.addWidget(self.save_credentials_checkbox)

        self.headless_checkbox = QCheckBox("Headless Mode")
        self.headless_checkbox.setToolTip("Run browser in background without showing the window")
        self.headless_checkbox.setChecked(True)
        toggles_layout.addWidget(self.headless_checkbox)
        toggles_layout.addStretch(1)

        header_color_label = QLabel("Header Color")
        header_color_label.setObjectName("FieldLabel")
        toggles_layout.addWidget(header_color_label)

        self.header_color_combo = QComboBox()
        self.header_color_combo.setMinimumHeight(34)
        self.header_color_combo.setMinimumWidth(162)
        self.header_color_combo.setMaximumWidth(184)
        self.header_color_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._configure_dropdown_combo(self.header_color_combo)
        self._populate_header_color_combo()
        self.header_color_combo.currentIndexChanged.connect(self._on_header_color_changed)
        toggles_layout.addWidget(self.header_color_combo)
        auth_layout.addLayout(toggles_layout)

        source_row_layout = QHBoxLayout()
        source_row_layout.setSpacing(10)

        source_label = QLabel("Source")
        source_label.setObjectName("FieldLabel")
        source_row_layout.addWidget(source_label)

        self.source_mode_combo = QComboBox()
        self.source_mode_combo.setMinimumHeight(34)
        self.source_mode_combo.setMinimumWidth(166)
        self.source_mode_combo.setMaximumWidth(194)
        self.source_mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._configure_dropdown_combo(self.source_mode_combo)
        self._populate_source_mode_combo()
        self.source_mode_combo.currentIndexChanged.connect(self._on_source_mode_changed)
        source_row_layout.addWidget(self.source_mode_combo)

        self.recipients_toggle_btn = QPushButton("Recipients")
        self.recipients_toggle_btn.setObjectName("InlineToggleButton")
        self.recipients_toggle_btn.setCheckable(True)
        self.recipients_toggle_btn.setToolTip(
            "Manually override the outgoing To and CC recipients for this run."
        )
        self.recipients_toggle_btn.toggled.connect(self._toggle_recipient_panel)
        source_row_layout.addWidget(self.recipients_toggle_btn)
        source_row_layout.addStretch(1)
        auth_layout.addLayout(source_row_layout)

        self.irt_filters_frame = QFrame()
        self.irt_filters_frame.setObjectName("InlineFiltersCard")
        irt_filters_layout = QHBoxLayout(self.irt_filters_frame)
        irt_filters_layout.setSpacing(10)
        irt_filters_layout.setContentsMargins(12, 10, 12, 10)

        irt_court_label = QLabel("Court")
        irt_court_label.setObjectName("FieldLabel")
        irt_filters_layout.addWidget(irt_court_label)

        self.irt_court_combo = QComboBox()
        self.irt_court_combo.setMinimumHeight(34)
        self.irt_court_combo.setMinimumWidth(144)
        self.irt_court_combo.setMaximumWidth(156)
        self._configure_dropdown_combo(self.irt_court_combo)
        self.irt_court_combo.addItem("Both Courts", "both")
        self.irt_court_combo.addItem("FDPLR000", "FDPLR000")
        self.irt_court_combo.addItem("FDCCA001", "FDCCA001")
        self.irt_court_combo.setToolTip("Choose which IRT court code to import.")
        irt_filters_layout.addWidget(self.irt_court_combo)

        irt_from_label = QLabel("From")
        irt_from_label.setObjectName("FieldLabel")
        irt_filters_layout.addWidget(irt_from_label)

        self.irt_start_date_edit = QDateEdit()
        self.irt_start_date_edit.setCalendarPopup(True)
        self.irt_start_date_edit.setDisplayFormat("M/d")
        self.irt_start_date_edit.setMinimumHeight(34)
        self.irt_start_date_edit.setMinimumWidth(108)
        self.irt_start_date_edit.setMaximumWidth(118)
        irt_filters_layout.addWidget(self.irt_start_date_edit)

        irt_to_label = QLabel("To")
        irt_to_label.setObjectName("FieldLabel")
        irt_filters_layout.addWidget(irt_to_label)

        self.irt_end_date_edit = QDateEdit()
        self.irt_end_date_edit.setCalendarPopup(True)
        self.irt_end_date_edit.setDisplayFormat("M/d")
        self.irt_end_date_edit.setMinimumHeight(34)
        self.irt_end_date_edit.setMinimumWidth(108)
        self.irt_end_date_edit.setMaximumWidth(118)
        irt_filters_layout.addWidget(self.irt_end_date_edit)
        irt_filters_layout.addStretch(1)
        self._apply_default_irt_filters()
        self.irt_filters_frame.setVisible(False)
        auth_layout.addWidget(self.irt_filters_frame)

        self.recipient_popup = RecipientOverridePopup(self)
        self._apply_card_shadow(self.recipient_popup)
        self.recipient_popup.closed.connect(self._on_recipient_popup_closed)
        self.recipient_to_input = self.recipient_popup.to_input
        self.recipient_cc_input = self.recipient_popup.cc_input

        self.compact_preview_checkbox = QCheckBox("Preview compact runtime UX")
        self.compact_preview_checkbox.setToolTip(
            "When enabled in Developer Mode, only two cards are shown at once."
        )
        self.compact_preview_checkbox.toggled.connect(self._on_compact_preview_toggled)
        self.compact_preview_checkbox.setVisible(False)
        auth_layout.addWidget(self.compact_preview_checkbox)

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)

        self.generate_btn = QPushButton("Manual Template")
        self.generate_btn.setObjectName("SecondaryButton")
        self.generate_btn.setToolTip("Optional fallback worksheet")
        self.generate_btn.clicked.connect(self.generate_template)
        buttons_layout.addWidget(self.generate_btn)

        self.view_folder_btn = QPushButton("Open Results")
        self.view_folder_btn.setObjectName("GhostButton")
        self.view_folder_btn.clicked.connect(self.view_output_folder)
        buttons_layout.addWidget(self.view_folder_btn)

        self.extract_btn = QPushButton("Run Extraction")
        self.extract_btn.setObjectName("PrimaryButton")
        self.extract_btn.setEnabled(False)
        self.extract_btn.clicked.connect(self.start_extraction)
        buttons_layout.addWidget(self.extract_btn, 1)

        auth_layout.addLayout(buttons_layout)

        main_layout.addWidget(auth_card)

        runtime_card = self._create_card()
        self.runtime_card = runtime_card
        runtime_layout = QVBoxLayout(runtime_card)
        runtime_layout.setSpacing(0)
        runtime_layout.setContentsMargins(18, 18, 18, 16)
        runtime_layout.addSpacing(20)

        status_row = QHBoxLayout()
        self.status_row_layout = status_row
        status_row.setSpacing(8)

        self.status_badge = QLabel()
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.status_badge.setMaximumWidth(220)
        status_row.addWidget(self.status_badge, 0)

        self.progress_meta_label = QLabel("Outlook intake runs automatically.")
        self.progress_meta_label.setObjectName("ProgressMeta")
        self.progress_meta_label.setWordWrap(True)
        status_row.addWidget(self.progress_meta_label, 1)
        runtime_layout.addLayout(status_row)
        runtime_layout.addSpacing(18)

        self.progress_bar = AnimatedProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        runtime_layout.addWidget(self.progress_bar)
        runtime_layout.addSpacing(16)

        self.status_label = QLabel()
        self.status_label.setObjectName("ProgressLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.status_label.setWordWrap(True)
        self.status_label.setContentsMargins(12, 0, 12, 0)
        runtime_layout.addWidget(self.status_label)
        runtime_layout.addStretch(1)

        stop_row = QHBoxLayout()
        stop_row.setContentsMargins(0, 8, 0, 0)
        stop_row.addStretch(1)
        self.stop_btn = QPushButton("Stop Run")
        self.stop_btn.setObjectName("StopButton")
        self.stop_btn.setVisible(False)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_extraction)
        stop_row.addWidget(self.stop_btn)
        stop_row.addStretch(1)
        runtime_layout.addLayout(stop_row)
        runtime_layout.addSpacing(8)

        main_layout.addWidget(runtime_card)

        self._set_developer_mode_state(False)
        self._set_runtime_panel_mode(show_runtime=False)

    def load_saved_credentials(self):
        """Load saved credentials from the secure credential store."""
        try:
            credentials = load_credentials()
        except Exception as e:
            self._set_status_state(
                "Attention",
                f"Could not load saved credentials: {e}",
                "error",
            )
            return

        if credentials:
            self.id_input.setText(credentials.username)
            # Do not auto-fill password for better security.
            # User can re-enter it when starting extraction.
            self.save_credentials_checkbox.setChecked(True)
            self._set_status_state(
                "Ready",
                "Saved ID loaded. Re-enter your password to start.",
                "ready",
            )
            self.on_credentials_changed()

    def on_credentials_changed(self):
        """Handle credentials input change."""
        has_id = bool(self.id_input.text().strip())
        has_password = bool(self.password_input.text())
        self.extract_btn.setEnabled(has_id and has_password)

    def generate_template(self):
        """Generate formatted Excel template file in run subfolder."""
        try:
            from utils.excel_handler import ExcelHandler
            from utils.file_manager import FileManager

            file_manager = FileManager()
            run_folder = file_manager.create_run_folder()
            excel_handler = ExcelHandler(header_fill_color=self.header_fill_color)
            template_path = excel_handler.create_template(run_folder)

            self._set_status_state(
                "Ready",
                f"Manual template created: {template_path.name}",
                "ready",
            )
            excel_handler.open_file(template_path)

            QMessageBox.information(
                self,
                "Template Generated",
                (
                    "Manual reference template created successfully!\n\n"
                    f"File location:\n{template_path}\n\n"
                    "The file has been opened for you.\n\n"
                    "If you want Run Extraction to use this workbook,\n"
                    "switch Source to Manual Template after you finish editing it."
                ),
            )

        except Exception as e:
            self._set_status_state(
                "Issue",
                f"Failed to generate template: {e}",
                "error",
            )
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to generate template:\n{e}",
            )

    def view_output_folder(self):
        """Open the output results folder."""
        try:
            from utils.file_manager import FileManager

            file_manager = FileManager()
            if file_manager.open_results_folder():
                self._set_status_state(
                    "Ready",
                    "Results folder opened.",
                    "ready",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    "Failed to open results folder. Please navigate to:\n"
                    + str(file_manager.get_results_folder()),
                )
        except Exception as e:
            self._set_status_state(
                "Issue",
                f"Failed to open folder: {e}",
                "error",
            )
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to open folder:\n{e}",
            )

    def stop_extraction(self):
        """Request a cooperative stop for the active automation run."""
        if self.extraction_thread is None:
            return

        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stopping...")
        self.progress_meta_label.setText(
            "Stop requested. Finishing the current step and saving partial progress."
        )
        self._set_status_state(
            "Stopping",
            "Finishing the current step, saving partial progress, and closing the run...",
            "warning",
        )
        self.extraction_thread.request_stop()

    def start_extraction(self):
        """Start the extraction process."""
        from utils.file_manager import FileManager
        from utils.logger import Logger

        user_id = self.id_input.text().strip()
        password = self.password_input.text()

        if not user_id or not password:
            QMessageBox.warning(
                self,
                "Missing Credentials",
                "Please enter both ID and Password.",
            )
            return

        recipient_to = self.recipient_to_input.toPlainText().strip()
        recipient_cc = self.recipient_cc_input.toPlainText().strip()

        if (recipient_to or recipient_cc) and not recipient_to:
            QMessageBox.warning(
                self,
                "Recipients",
                "Enter at least one To recipient when using the manual Recipients override.",
            )
            return

        if self.developer_mode_enabled and not recipient_to:
            QMessageBox.warning(
                self,
                "Developer Mode",
                "Enter at least one To recipient in Recipients before running the test send.",
            )
            return

        file_manager = FileManager()
        source_mode = self._current_source_mode()
        excel_path = None
        irt_court_scope = "both"
        irt_start_date = None
        irt_end_date = None

        if source_mode == "irt":
            irt_court_scope = str(
                self.irt_court_combo.currentData(Qt.ItemDataRole.UserRole) or "both"
            )
            irt_start_date = self.irt_start_date_edit.date().toPyDate()
            irt_end_date = self.irt_end_date_edit.date().toPyDate()
            if irt_start_date > irt_end_date:
                QMessageBox.warning(
                    self,
                    "IRT Date Range",
                    "The IRT start date must be on or before the end date.",
                )
                return

        if source_mode == "manual":
            excel_path = file_manager.find_most_recent_excel_file()
            if excel_path is None:
                QMessageBox.warning(
                    self,
                    "No Manual Workbook Found",
                    "Please generate and fill out a Manual Template first, then close the file before running extraction.",
                )
                return

            if file_manager.is_file_locked(excel_path):
                QMessageBox.warning(
                    self,
                    "Manual Workbook Is Open",
                    "Please close the manual workbook first, then click Run Extraction again.",
                )
                return

            run_folder = excel_path.parent
        else:
            run_folder = file_manager.create_run_folder()

        logger = Logger(file_manager)
        logger.initialize_log_file(run_folder)
        logger.log("Extraction started")
        if source_mode == "manual":
            logger.log(f"Source workbook will be loaded from Manual Template: {excel_path}")
            if not self.developer_mode_enabled:
                logger.log(
                    "Outlook reply context will still be resolved from the latest matching email"
                )
        elif source_mode == "irt":
            logger.log("Source data will be imported from IRT Search Inventory")
            logger.log(
                "IRT filters selected: "
                f"court_scope={irt_court_scope} "
                f"start={irt_start_date.strftime('%m/%d/%Y')} "
                f"end={irt_end_date.strftime('%m/%d/%Y')}"
            )
        else:
            logger.log("Source data will be imported from Outlook email")
        logger.log(f"Workbook header color selected: #{self.header_fill_color}")
        if recipient_to:
            logger.log(
                (
                    "Developer mode enabled; outgoing email will use the Recipients override "
                    if self.developer_mode_enabled
                    else "Manual recipient override enabled; outgoing email will be sent "
                )
                + f"to To='{recipient_to}' CC='{recipient_cc}'"
            )
        if self.developer_mode_enabled and recipient_to:
            logger.log(
                "Developer mode is enabled and the Recipients override is controlling the safe test routing"
            )

        headless_mode = self.headless_checkbox.isChecked()
        remember_credentials = self.save_credentials_checkbox.isChecked()

        self.id_input.setEnabled(False)
        self.password_input.setEnabled(False)
        self.save_credentials_checkbox.setEnabled(False)
        self.headless_checkbox.setEnabled(False)
        self.header_color_combo.setEnabled(False)
        self.source_mode_combo.setEnabled(False)
        self.irt_filters_frame.setEnabled(False)
        self.recipients_toggle_btn.setEnabled(False)
        self.recipient_popup.hide()
        self.developer_mode_shortcut.setEnabled(False)
        self.generate_btn.setEnabled(False)
        self.extract_btn.setEnabled(False)
        self.stop_btn.setText("Stop Run")
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self._set_status_state(
            "Syncing",
            (
                "Opening manual workbook and starting extraction..."
                if source_mode == "manual"
                else (
                    "Importing IRT results and starting extraction..."
                    if source_mode == "irt"
                    else "Importing Outlook data and starting extraction..."
                )
            ),
            "working",
        )
        self._set_runtime_panel_mode(show_runtime=True)

        self.extraction_thread = ExtractionThread(
            user_id=user_id,
            password=password,
            excel_path=excel_path,
            logger=logger,
            file_manager=file_manager,
            headless_mode=headless_mode,
            run_folder=run_folder,
            remember_credentials=remember_credentials,
            developer_mode_enabled=self.developer_mode_enabled,
            developer_override_to="",
            developer_override_cc="",
            manual_override_to=recipient_to,
            manual_override_cc=recipient_cc,
            header_fill_color=self.header_fill_color,
            source_mode=source_mode,
            irt_court_scope=irt_court_scope,
            irt_start_date=irt_start_date,
            irt_end_date=irt_end_date,
        )
        self.extraction_thread.progress_update.connect(self.update_progress)
        self.extraction_thread.finished.connect(self.extraction_finished)
        self.extraction_thread.cancelled.connect(self.extraction_cancelled)
        self.extraction_thread.start()

    def update_progress(self, percentage: int, status: str):
        """Update progress bar and status."""
        self.progress_bar.setValue(percentage)
        self._set_status_state("Running", status, "working")

    def reset_ui(self):
        """Reset UI to initial state for next run."""
        self.progress_bar.setValue(0)

        self.id_input.setEnabled(True)
        self.password_input.setEnabled(True)
        self.save_credentials_checkbox.setEnabled(True)
        self.headless_checkbox.setEnabled(True)
        self.header_color_combo.setEnabled(True)
        self.source_mode_combo.setEnabled(True)
        self.irt_filters_frame.setEnabled(True)
        self.recipients_toggle_btn.setEnabled(True)
        self.developer_mode_shortcut.setEnabled(True)
        self.generate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.stop_btn.setText("Stop Run")

        self.on_credentials_changed()
        self._set_developer_mode_state(self.developer_mode_enabled)
        self._set_runtime_panel_mode(show_runtime=False)

    def extraction_finished(self, success: bool, message: str, output_path):
        """Handle extraction completion."""
        self.progress_bar.setValue(0)

        if success:
            self._set_status_state("Completed", message, "success")
            QMessageBox.information(
                self,
                "Lexis Cite Extraction Successful!",
                message,
            )
            self.open_output_file(output_path)
        else:
            self._set_status_state("Issue", message, "error")
            QMessageBox.critical(
                self,
                "Extraction Failed",
                message,
            )

        self.reset_ui()
        self.extraction_thread = None

    def extraction_cancelled(self, message: str, output_path):
        """Handle a user-requested stop without treating it like a crash."""
        self.progress_bar.setValue(0)
        self._set_status_state("Stopped", message, "warning")
        QMessageBox.information(
            self,
            "Run Stopped",
            message,
        )
        self.reset_ui()
        self.extraction_thread = None

    def open_output_file(self, output_path):
        """Open the output Excel file after the user acknowledges success."""
        if output_path is None:
            return

        path = Path(output_path)
        if not path.exists():
            QMessageBox.warning(
                self,
                "Output File Not Found",
                f"The extraction finished, but the output file could not be found:\n{path}",
            )
            return

        try:
            from utils.excel_handler import ExcelHandler

            excel_handler = ExcelHandler()
            excel_handler.open_file(path)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Could Not Open Output File",
                f"The extraction finished, but the output file could not be opened:\n{e}",
            )
