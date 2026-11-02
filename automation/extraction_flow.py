"""Bounded retries with a fresh search page after a technical failure."""
from utils.extraction_result import EXTRACTED, NO_RESULTS, UNVERIFIED, ExtractionResult


def extract_lni(scraper, lni: str, attempts: int = 2) -> ExtractionResult:
    result = ExtractionResult(UNVERIFIED, detail="Search was not attempted")
    for attempt in range(1, attempts + 1):
        scraper._raise_if_cancelled()
        if attempt > 1:
            scraper._log(f"Retrying LNI {lni} on a fresh search page ({attempt}/{attempts})")
        try:
            if not scraper.prepare_search(force_reset=attempt > 1):
                result = ExtractionResult(UNVERIFIED, detail="Search page could not be restored")
                continue
            result = scraper.extract_current_lni(lni)
            if result.status in {EXTRACTED, NO_RESULTS}:
                return result
        except InterruptedError:
            raise
        except Exception as error:
            result = ExtractionResult(UNVERIFIED, detail=f"{type(error).__name__}: {error}")
            scraper._log_page_diagnostics("Extraction exception")
    scraper._log(f"LNI {lni}: {result.status} after {attempts} attempts | {result.detail}")
    return result
