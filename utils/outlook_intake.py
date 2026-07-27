"""
Outlook inbox intake for automatically building the extraction workbook.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup


OUTLOOK_INBOX_FOLDER = 6
OUTLOOK_MAIL_CLASS = 43
SUBJECT_FRAGMENT = "plr000/cca001 for conversion"
BODY_FRAGMENT = "sent to the keying vendor."
REQUIRED_HEADERS = ("LNI", "FILENAME", "COURT", "DECIDED DATE")
OPTIONAL_HEADERS = ("LEXIS CITE",)
ALLOWED_HEADERS = set(REQUIRED_HEADERS + OPTIONAL_HEADERS)


class OutlookImportError(RuntimeError):
    """Raised when the source workbook cannot be imported from Outlook."""


@dataclass(frozen=True)
class OutlookSourceRow:
    """Represents one source-data row imported from Outlook."""

    lni: str
    filename: str
    court: str
    decided_date: str
    lexis_cite: str = ""

    def as_excel_record(self) -> dict[str, str]:
        """Return the row in Excel-header form."""
        return {
            "LNI": self.lni,
            "FILENAME": self.filename,
            "COURT": self.court,
            "DECIDED DATE": self.decided_date,
            "LEXIS CITE": self.lexis_cite,
        }


@dataclass(frozen=True)
class OutlookImportSummary:
    """Metadata for the imported Outlook source email and workbook."""

    workbook_path: Path
    imported_row_count: int
    email_subject: str
    sender_name: str
    received_time: Any
    selected_headers: list[str]
    message_entry_id: str
    message_store_id: str
    to_recipients: str
    cc_recipients: str


@dataclass(frozen=True)
class OutlookMessageContext:
    """Reply-thread metadata for the matching Outlook source email."""

    email_subject: str
    sender_name: str
    received_time: Any
    selected_headers: list[str]
    message_entry_id: str
    message_store_id: str
    to_recipients: str
    cc_recipients: str


def _log(logger: Any | None, message: str) -> None:
    """Write a log entry when a logger is available."""
    if logger:
        logger.log(message)


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    """Stop promptly when the UI requests cancellation."""
    if cancel_check and cancel_check():
        raise InterruptedError("Run stopped by user.")


def _normalize_text(value: Any) -> str:
    """Normalize text for robust matching and workbook output."""
    if value is None:
        return ""

    return " ".join(str(value).replace("\xa0", " ").split()).strip()


def _normalize_header(value: Any) -> str:
    """Normalize a header value for case-insensitive matching."""
    return _normalize_text(value).upper()


def _parse_html_tables(html_body: str) -> list[list[list[str]]]:
    """Parse all HTML tables in the message body into row/cell text arrays."""
    soup = BeautifulSoup(html_body or "", "html.parser")
    parsed_tables: list[list[list[str]]] = []

    for table in soup.find_all("table"):
        parsed_rows: list[list[str]] = []

        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if not cells:
                continue

            parsed_rows.append([
                _normalize_text(cell.get_text(" ", strip=True))
                for cell in cells
            ])

        if parsed_rows:
            parsed_tables.append(parsed_rows)

    return parsed_tables


def _is_header_like_row(row: list[str]) -> bool:
    """Detect repeated header rows that should be skipped during import."""
    normalized = tuple(_normalize_header(value) for value in row[: len(REQUIRED_HEADERS)])
    return normalized == REQUIRED_HEADERS


def _table_score(table_rows: list[list[str]]) -> tuple[int, int, int, int, int] | None:
    """
    Score a parsed HTML table for suitability as the source data table.

    Higher is better. Exact 4-column source tables are preferred over reply
    tables that include the extra LEXIS CITE column.
    """
    if not table_rows:
        return None

    headers = [_normalize_header(value) for value in table_rows[0]]
    header_set = set(headers)

    if not set(REQUIRED_HEADERS).issubset(header_set):
        return None

    exact_required_match = headers == list(REQUIRED_HEADERS)
    prefix_matches = sum(
        1
        for index, required_header in enumerate(REQUIRED_HEADERS)
        if index < len(headers) and headers[index] == required_header
    )
    extra_headers = sum(1 for header in headers if header not in ALLOWED_HEADERS)
    distance_from_expected = abs(len(headers) - len(REQUIRED_HEADERS))
    data_row_count = sum(1 for row in table_rows[1:] if any(_normalize_text(value) for value in row))

    return (
        1 if exact_required_match else 0,
        prefix_matches,
        -distance_from_expected,
        -extra_headers,
        data_row_count,
    )


def _extract_source_rows(table_rows: list[list[str]]) -> tuple[list[str], list[OutlookSourceRow]]:
    """Convert the selected table into strongly typed source rows."""
    if not table_rows:
        raise OutlookImportError("The selected Outlook table was empty.")

    raw_headers = [_normalize_text(value) for value in table_rows[0]]
    normalized_headers = [_normalize_header(value) for value in raw_headers]
    header_index = {header: index for index, header in enumerate(normalized_headers)}

    source_rows: list[OutlookSourceRow] = []

    for raw_row in table_rows[1:]:
        padded_row = list(raw_row) + [""] * max(0, len(raw_headers) - len(raw_row))

        if _is_header_like_row(padded_row):
            continue

        row_values = {
            header: _normalize_text(padded_row[index]) if index < len(padded_row) else ""
            for header, index in header_index.items()
        }

        required_values = [row_values.get(header, "") for header in REQUIRED_HEADERS]
        if not any(required_values):
            continue

        source_rows.append(
            OutlookSourceRow(
                lni=row_values.get("LNI", ""),
                filename=row_values.get("FILENAME", ""),
                court=row_values.get("COURT", ""),
                decided_date=row_values.get("DECIDED DATE", ""),
                lexis_cite=row_values.get("LEXIS CITE", ""),
            )
        )

    if not source_rows:
        raise OutlookImportError("No source data rows were found in the selected Outlook table.")

    return raw_headers, source_rows


def _select_best_source_table(html_body: str) -> tuple[list[str], list[OutlookSourceRow]]:
    """Choose the best source-data table from the email HTML body."""
    parsed_tables = _parse_html_tables(html_body)

    best_table: list[list[str]] | None = None
    best_score: tuple[int, int, int, int, int] | None = None

    for table_rows in parsed_tables:
        score = _table_score(table_rows)
        if score is None:
            continue

        if best_score is None or score > best_score:
            best_score = score
            best_table = table_rows

    if best_table is None:
        raise OutlookImportError(
            "No table with the required headers was found in the matching Outlook email."
        )

    return _extract_source_rows(best_table)


def _open_inbox_window(application: Any, inbox: Any, logger: Any | None = None) -> None:
    """Try to show Outlook focused on the Inbox without failing the import if it cannot."""
    try:
        explorer = application.ActiveExplorer()
        if explorer is None:
            explorer = application.Explorers.Add(inbox, 0)

        explorer.CurrentFolder = inbox
        explorer.Display()
        _log(logger, "Opened Outlook Inbox")
    except Exception as e:
        _log(logger, f"Could not display Outlook Inbox window: {e}")


def _build_message_context(
    item: Any,
    headers: list[str],
) -> OutlookMessageContext:
    """Capture plain metadata from the matched Outlook email."""
    subject = _normalize_text(getattr(item, "Subject", ""))
    sender_name = _normalize_text(getattr(item, "SenderName", ""))
    received_time = getattr(item, "ReceivedTime", None)
    message_entry_id = _normalize_text(getattr(item, "EntryID", ""))
    message_store_id = _normalize_text(getattr(getattr(item, "Parent", None), "StoreID", ""))
    to_recipients = _normalize_text(getattr(item, "To", ""))
    cc_recipients = _normalize_text(getattr(item, "CC", ""))

    return OutlookMessageContext(
        email_subject=subject,
        sender_name=sender_name,
        received_time=received_time,
        selected_headers=headers,
        message_entry_id=message_entry_id,
        message_store_id=message_store_id,
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
    )


def _find_matching_conversion_email_data(
    logger: Any | None,
    subject_fragment: str,
    body_fragment: str,
    max_items_to_scan: int,
    open_inbox: bool,
    validate_sender_not_me: bool,
    validate_not_replied: bool,
    cancel_check: Callable[[], bool] | None,
) -> tuple[OutlookMessageContext, list[OutlookSourceRow]]:
    """Locate the latest matching email and return its thread context plus source rows."""
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OutlookImportError(
            "Outlook import requires pywin32, but it is not installed."
        ) from exc

    normalized_subject_fragment = subject_fragment.lower()
    normalized_body_fragment = body_fragment.lower()

    if not validate_sender_not_me:
        _log(logger, "Sender-not-me validation is disabled for this test run")
    if not validate_not_replied:
        _log(logger, "Reply-status validation is disabled for this test run")

    _raise_if_cancelled(cancel_check)
    pythoncom.CoInitialize()

    try:
        application = win32com.client.Dispatch("Outlook.Application")
        namespace = application.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(OUTLOOK_INBOX_FOLDER)

        if open_inbox:
            _open_inbox_window(application, inbox, logger)

        items = inbox.Items
        items.Sort("[ReceivedTime]", True)

        checked_count = 0

        for item in items:
            _raise_if_cancelled(cancel_check)
            if getattr(item, "Class", None) != OUTLOOK_MAIL_CLASS:
                continue

            checked_count += 1

            subject = _normalize_text(getattr(item, "Subject", ""))
            body = _normalize_text(getattr(item, "Body", ""))

            if normalized_subject_fragment not in subject.lower():
                if checked_count >= max_items_to_scan:
                    break
                continue

            if normalized_body_fragment not in body.lower():
                if checked_count >= max_items_to_scan:
                    break
                continue

            try:
                headers, source_rows = _select_best_source_table(
                    str(getattr(item, "HTMLBody", "") or "")
                )
            except OutlookImportError as e:
                _log(logger, f"Skipping matching email without usable source table: {e}")
                if checked_count >= max_items_to_scan:
                    break
                continue

            return _build_message_context(item, headers), source_rows

        raise OutlookImportError(
            "No recent Outlook email matched the required subject/body criteria with a usable table."
        )

    finally:
        pythoncom.CoUninitialize()


def find_conversion_email_context(
    logger: Any | None = None,
    subject_fragment: str = SUBJECT_FRAGMENT,
    body_fragment: str = BODY_FRAGMENT,
    max_items_to_scan: int = 400,
    open_inbox: bool = False,
    validate_sender_not_me: bool = False,
    validate_not_replied: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> OutlookMessageContext:
    """Find the latest matching Outlook email and return only its reply-thread metadata."""
    message_context, source_rows = _find_matching_conversion_email_data(
        logger=logger,
        subject_fragment=subject_fragment,
        body_fragment=body_fragment,
        max_items_to_scan=max_items_to_scan,
        open_inbox=open_inbox,
        validate_sender_not_me=validate_sender_not_me,
        validate_not_replied=validate_not_replied,
        cancel_check=cancel_check,
    )
    _log(
        logger,
        f"Matched Outlook reply context from email: {message_context.email_subject}",
    )
    _log(
        logger,
        f"Reply context source table headers: {', '.join(message_context.selected_headers)}",
    )
    _log(
        logger,
        f"Reply context email contains {len(source_rows)} source row(s)",
    )
    return message_context


def import_conversion_email_to_workbook(
    run_folder: Path,
    logger: Any | None = None,
    header_fill_color: str | None = None,
    subject_fragment: str = SUBJECT_FRAGMENT,
    body_fragment: str = BODY_FRAGMENT,
    max_items_to_scan: int = 400,
    open_inbox: bool = True,
    validate_sender_not_me: bool = False,
    validate_not_replied: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> OutlookImportSummary:
    """
    Find the latest matching Outlook email and build a clean workbook from its table.

    Sender/reply validations are intentionally disabled by default for the current
    testing phase. The flags are accepted here so we can turn them on later
    without redesigning the workflow.
    """
    from utils.excel_handler import ExcelHandler

    run_folder.mkdir(parents=True, exist_ok=True)
    message_context, source_rows = _find_matching_conversion_email_data(
        logger=logger,
        subject_fragment=subject_fragment,
        body_fragment=body_fragment,
        max_items_to_scan=max_items_to_scan,
        open_inbox=open_inbox,
        validate_sender_not_me=validate_sender_not_me,
        validate_not_replied=validate_not_replied,
        cancel_check=cancel_check,
    )

    excel_handler = ExcelHandler(header_fill_color=header_fill_color)
    workbook_path = excel_handler.create_template(run_folder)
    _raise_if_cancelled(cancel_check)
    excel_handler.open_excel_file(workbook_path)
    excel_handler.populate_source_rows(
        [row.as_excel_record() for row in source_rows]
    )
    _raise_if_cancelled(cancel_check)
    excel_handler.save(workbook_path)

    _log(
        logger,
        f"Imported {len(source_rows)} row(s) from Outlook email: {message_context.email_subject}",
    )
    _log(
        logger,
        f"Selected source table headers: {', '.join(message_context.selected_headers)}",
    )

    return OutlookImportSummary(
        workbook_path=workbook_path,
        imported_row_count=len(source_rows),
        email_subject=message_context.email_subject,
        sender_name=message_context.sender_name,
        received_time=message_context.received_time,
        selected_headers=message_context.selected_headers,
        message_entry_id=message_context.message_entry_id,
        message_store_id=message_context.message_store_id,
        to_recipients=message_context.to_recipients,
        cc_recipients=message_context.cc_recipients,
    )
