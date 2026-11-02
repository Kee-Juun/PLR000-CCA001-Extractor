"""Run a small Lexis sample without importing or calling any email sender."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time

from automation.extraction_flow import extract_lni
from automation.lexis_scraper import LexisScraper
from config.credentials import load_credentials
from utils.excel_handler import ExcelHandler
from utils.extraction_result import EXTRACTED, UNVERIFIED, NOT_CHECKED, ExtractionResult, classify_saved_result
from utils.logger import Logger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--rows", help="Excel row numbers to check (default: 2,3,48)")
    selection.add_argument("--unresolved", action="store_true", help="Recheck all unresolved rows into a separate workbook, with no email")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/diagnostics"))
    args = parser.parse_args()
    selected = [int(value) for value in (args.rows or "2,3,48").split(",")]
    if not selected or len(selected) > 5 or any(row < 2 for row in selected):
        parser.error("Select between one and five data rows (row 2 or later)")
    credentials = load_credentials()
    if not credentials or not credentials.password:
        parser.error("No saved Lexis login is available. Save it using the application first.")
    folder = args.output_dir.resolve() / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder.mkdir(parents=True)
    logger = Logger()
    logger.initialize_log_file(folder)
    logger.log("Diagnostic sample: no email; source workbook is read only")
    handler = ExcelHandler()
    handler.open_excel_file(args.workbook)
    source = dict(handler.read_lni_data())
    output_workbook = None
    if args.unresolved:
        selected = []
        has_status = handler.worksheet['F1'].value == 'EXTRACTION STATUS'
        for row in source:
            cite = handler.worksheet[f'E{row}'].value
            status = handler.worksheet[f'F{row}'].value if has_status else ''
            if classify_saved_result(cite, status) == EXTRACTED:
                handler.write_extraction_result(row, ExtractionResult(EXTRACTED, str(cite), "Prior citation retained; not rechecked in this run"))
            else:
                selected.append(row)
                handler.write_extraction_result(row, ExtractionResult(NOT_CHECKED, detail="Not yet reached in this recheck"))
        output_workbook = folder / ("Rechecked_" + args.workbook.name)
        handler.save(output_workbook)
    samples = [(row, source[row]) for row in selected if row in source]
    if not args.unresolved:
        handler.workbook.close()
    if len(samples) != len(selected):
        parser.error("One or more selected rows have no LNI")
    deadline = time.monotonic() + (3600 if args.unresolved else 600)
    scraper = LexisScraper(logger, cancel_check=lambda: time.monotonic() >= deadline)
    results = []
    failure = ""
    consecutive_failures = 0
    try:
        if not scraper.launch_browser(headless_mode=args.headless) or not scraper.navigate_to_lexis():
            raise RuntimeError("Could not launch or navigate the browser")
        login = scraper.login(credentials.username, credentials.password)
        if not login.success:
            raise RuntimeError(f"Login failed: {login.reason}")
        for row, lni in samples:
            logger.log(f"Checking row {row} ({len(results) + 1}/{len(samples)}): {lni}")
            result = extract_lni(scraper, lni)
            results.append({"row": row, "lni": lni, "status": result.status,
                            "cite": result.cite, "detail": result.detail})
            if not args.unresolved:
                scraper._save_debug_screenshot(f"sample_row_{row}_{result.status}")
            else:
                handler.write_extraction_result(row, result)
                handler.save(output_workbook)
            (folder / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            consecutive_failures = consecutive_failures + 1 if result.status == UNVERIFIED else 0
            if consecutive_failures >= 3:
                failure = "Stopped after 3 consecutive technical failures; remaining rows are Not checked"
                logger.log(failure)
                break
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
        logger.log(failure)
    finally:
        scraper.close_browser()
        (folder / "summary.json").write_text(json.dumps({
            "headless": args.headless, "email_enabled": False, "failure": failure, "results": results,
            "requested_rows": len(samples), "checked_rows": len(results), "workbook": str(output_workbook or ''),
        }, indent=2), encoding="utf-8")
        if args.unresolved:
            handler.workbook.close()
        logger.save_log()
    print(f"Diagnostic output: {folder}")
    return int(bool(failure) or any(item["status"] == "Unverified" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
