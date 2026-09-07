"""Outlook-based run status reporting for PLR000-CCA001 Extractor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import socket
from pathlib import Path
from typing import Any

from config.app_settings import (
    RUN_REPORT_DEVELOPER_EMAIL_KEY,
    RUN_REPORT_EMAIL_ENABLED_KEY,
    RUN_REPORT_USER_EMAIL_KEY,
    load_config_data,
)


APP_NAME = "PLR000-CCA001 Extractor"
DEFAULT_DEVELOPER_EMAIL = "kevinjohn.libuna@reedelsevier.com"

STATUS_LABELS = {
    "success": "Completed",
    "failure": "Failed",
    "cancelled": "Stopped",
}

SOURCE_LABELS = {
    "irt": "IRT Results",
    "manual": "Template",
    "outlook": "Outlook Email",
}


@dataclass(slots=True)
class RunStatusReportSummary:
    """Outcome of an automated run status email attempt."""

    sent: bool
    recipients: list[str]
    subject: str
    summary_path: Path | None
    payload_path: Path | None


def _log(logger: Any | None, message: str) -> None:
    """Write a log entry when a logger is available."""
    if logger:
        logger.log(message)


def _safe_filename(value: str) -> str:
    """Return a filesystem-safe filename fragment."""
    cleaned = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "_" for char in str(value or ""))
    cleaned = "_".join(cleaned.split())
    return cleaned.strip("_") or "report"


def _valid_email(value: str) -> bool:
    """Return True when the value looks like an email address."""
    text = str(value or "").strip()
    return "@" in text and "." in text.split("@", 1)[-1]


def _get_outlook_user_email(outlook: Any) -> str:
    """Return the current Outlook session email when possible."""
    try:
        session = outlook.Session
        for index in range(1, session.Accounts.Count + 1):
            account = session.Accounts.Item(index)
            smtp_address = getattr(account, "SmtpAddress", "") or ""
            if smtp_address:
                return str(smtp_address).strip()
    except Exception:
        pass

    try:
        address_entry = outlook.Session.CurrentUser.AddressEntry
        if address_entry.Type == "EX":
            exchange_user = address_entry.GetExchangeUser()
            if exchange_user and exchange_user.PrimarySmtpAddress:
                return str(exchange_user.PrimarySmtpAddress).strip()
        return str(address_entry.Address or "").strip()
    except Exception:
        return ""


def _tail_text(path: Path | None, max_lines: int = 30) -> str:
    """Return a short tail of a text file."""
    if path is None or not path.exists():
        return ""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""

    if not lines:
        return ""

    tail = lines[-max_lines:]
    return "\n".join(tail)


def _resolve_report_dir(
    run_folder: Path | None,
    workbook_path: Path | None,
    log_file_path: Path | None,
) -> Path:
    """Return the best folder for report artifacts."""
    for candidate in (run_folder, workbook_path.parent if workbook_path else None, log_file_path.parent if log_file_path else None):
        if candidate is None:
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    return Path.cwd()


def _build_subject(status_label: str, timestamp: datetime) -> str:
    """Build the Outlook subject line."""
    return f"{APP_NAME}: {status_label} - {timestamp.strftime('%Y-%m-%d %I:%M:%S %p')}"


def _build_payload(
    *,
    status: str,
    message: str,
    run_folder: Path | None,
    workbook_path: Path | None,
    log_file_path: Path | None,
    user_id: str,
    source_mode: str,
    headless_mode: bool,
    developer_mode_enabled: bool,
    recipient_override_to: str,
    recipient_override_cc: str,
    current_outlook_user: str,
) -> dict[str, Any]:
    """Build the serialized run report payload."""
    timestamp = datetime.now()
    status_label = STATUS_LABELS.get(status, status.title())
    source_label = SOURCE_LABELS.get(str(source_mode or "").strip().lower(), str(source_mode or "").strip() or "Unknown")

    return {
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "status": status_label,
        "status_code": status,
        "message": str(message or "").strip(),
        "machine": socket.gethostname(),
        "windows_user": os.environ.get("USERNAME", ""),
        "outlook_user": current_outlook_user,
        "lexis_id": str(user_id or "").strip(),
        "source_mode": source_label,
        "headless_mode": bool(headless_mode),
        "developer_mode_enabled": bool(developer_mode_enabled),
        "recipient_override_to": str(recipient_override_to or "").strip(),
        "recipient_override_cc": str(recipient_override_cc or "").strip(),
        "run_folder": str(run_folder or ""),
        "workbook_path": str(workbook_path or ""),
        "log_file_path": str(log_file_path or ""),
    }


def _build_body(payload: dict[str, Any], recent_log_lines: str) -> str:
    """Build a concise plain-text Outlook body."""
    override_to = payload.get("recipient_override_to") or ""
    override_cc = payload.get("recipient_override_cc") or ""
    override_line = "None"
    if override_to or override_cc:
        override_line = f"To={override_to or 'N/A'} | CC={override_cc or 'N/A'}"

    lines = [
        f"{APP_NAME} finished on a user's machine.",
        "",
        f"Status: {payload.get('status')}",
        f"Summary: {payload.get('message') or 'No additional summary was provided.'}",
        "",
        "Run Context",
        "-----------",
        f"Timestamp: {payload.get('timestamp')}",
        f"Windows user: {payload.get('windows_user') or 'N/A'}",
        f"Outlook user: {payload.get('outlook_user') or 'N/A'}",
        f"Lexis ID: {payload.get('lexis_id') or 'N/A'}",
        f"Source: {payload.get('source_mode') or 'N/A'}",
        f"Headless mode: {'Yes' if payload.get('headless_mode') else 'No'}",
        f"Developer mode: {'Enabled' if payload.get('developer_mode_enabled') else 'Disabled'}",
        f"Recipient override: {override_line}",
        f"Run folder: {payload.get('run_folder') or 'N/A'}",
        f"Workbook: {payload.get('workbook_path') or 'N/A'}",
        f"Log: {payload.get('log_file_path') or 'N/A'}",
    ]

    if recent_log_lines:
        lines.extend(
            [
                "",
                "Recent Log Lines",
                "----------------",
                recent_log_lines,
            ]
        )

    lines.extend(
        [
            "",
            "Attached when available:",
            "- Run Event Summary.txt",
            "- Run Event Payload.json",
            "- Current run log",
            "- Workbook/output file",
        ]
    )
    return "\n".join(lines)


def _write_summary_files(report_dir: Path, payload: dict[str, Any], recent_log_lines: str) -> tuple[Path | None, Path | None]:
    """Write report artifacts beside the run output."""
    timestamp = datetime.fromisoformat(payload["timestamp"]).strftime("%Y-%m-%d_%I-%M-%S_%p")
    stem = f"{timestamp}_{_safe_filename(payload.get('status', 'Status'))}"
    summary_path = report_dir / f"Run Event Summary - {stem}.txt"
    payload_path = report_dir / f"Run Event Payload - {stem}.json"

    try:
        summary_path.write_text(_build_body(payload, recent_log_lines), encoding="utf-8")
    except Exception:
        summary_path = None

    try:
        payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        payload_path = None

    return summary_path, payload_path


def _send_outlook_email(
    subject: str,
    body: str,
    attachments: list[Path],
    recipients: list[str],
    logger: Any | None = None,
) -> bool:
    """Send the email using the user's Outlook session."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        try:
            session = outlook.Session
            if session.Accounts.Count >= 1:
                mail.SendUsingAccount = session.Accounts.Item(1)
        except Exception:
            pass

        recipients_text = "; ".join(recipients)
        mail.To = recipients_text
        mail.Subject = subject
        mail.Body = body
        mail.Save()
        for attachment in attachments:
            if attachment.exists():
                mail.Attachments.Add(str(attachment.resolve()))
        mail.Save()
        mail.Send()
        _log(logger, f"Run status report email sent to: {recipients_text}")
        return True
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def send_run_status_report(
    *,
    status: str,
    message: str,
    run_folder: Path | None,
    workbook_path: Path | None,
    log_file_path: Path | None,
    user_id: str,
    source_mode: str,
    headless_mode: bool,
    developer_mode_enabled: bool,
    recipient_override_to: str = "",
    recipient_override_cc: str = "",
    logger: Any | None = None,
    config: dict[str, Any] | None = None,
) -> RunStatusReportSummary:
    """Create and send a terminal run status email."""
    config_data = dict(config or load_config_data())
    if not config_data.get(RUN_REPORT_EMAIL_ENABLED_KEY, True):
        _log(logger, "Run status report email skipped because it is disabled in config.")
        return RunStatusReportSummary(False, [], "", None, None)

    developer_email = str(config_data.get(RUN_REPORT_DEVELOPER_EMAIL_KEY, "") or "").strip()
    if not developer_email:
        developer_email = DEFAULT_DEVELOPER_EMAIL

    user_email = str(config_data.get(RUN_REPORT_USER_EMAIL_KEY, "") or "").strip()
    recipients: list[str] = []
    if _valid_email(developer_email):
        recipients.append(developer_email)
    if _valid_email(user_email) and user_email.lower() not in {item.lower() for item in recipients}:
        recipients.append(user_email)

    if not recipients:
        _log(logger, "Run status report email skipped because no valid recipients are configured.")
        return RunStatusReportSummary(False, [], "", None, None)

    current_outlook_user = ""
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            current_outlook_user = _get_outlook_user_email(outlook)
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    except Exception:
        current_outlook_user = ""

    resolved_workbook = Path(workbook_path) if workbook_path else None
    resolved_log = Path(log_file_path) if log_file_path else None
    resolved_run_folder = Path(run_folder) if run_folder else None
    report_dir = _resolve_report_dir(resolved_run_folder, resolved_workbook, resolved_log)

    payload = _build_payload(
        status=status,
        message=message,
        run_folder=resolved_run_folder,
        workbook_path=resolved_workbook,
        log_file_path=resolved_log,
        user_id=user_id,
        source_mode=source_mode,
        headless_mode=headless_mode,
        developer_mode_enabled=developer_mode_enabled,
        recipient_override_to=recipient_override_to,
        recipient_override_cc=recipient_override_cc,
        current_outlook_user=current_outlook_user,
    )
    recent_log_lines = _tail_text(resolved_log)
    summary_path, payload_path = _write_summary_files(report_dir, payload, recent_log_lines)

    status_label = payload["status"]
    subject = _build_subject(status_label, datetime.fromisoformat(payload["timestamp"]))
    body = _build_body(payload, recent_log_lines)
    attachments = [
        path
        for path in (
            summary_path,
            payload_path,
            resolved_log if resolved_log and resolved_log.exists() else None,
            resolved_workbook if resolved_workbook and resolved_workbook.exists() else None,
        )
        if path is not None
    ]

    try:
        sent = _send_outlook_email(subject, body, attachments, recipients, logger=logger)
    except Exception as exc:
        _log(logger, f"Run status report could not be sent automatically: {exc}")
        sent = False

    return RunStatusReportSummary(
        sent=sent,
        recipients=recipients,
        subject=subject,
        summary_path=summary_path,
        payload_path=payload_path,
    )
