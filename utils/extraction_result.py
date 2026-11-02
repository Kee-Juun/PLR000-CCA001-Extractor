"""Explicit outcomes shared by browser extraction, workbooks and email."""
from dataclasses import dataclass
import re

EXTRACTED = "Extracted"
NO_RESULTS = "No results"
UNVERIFIED = "Unverified"
NOT_CHECKED = "Not checked"
CITE_PATTERN = re.compile(r"\b\d{4}\s+(?:IRS\s+)?(?:PLR|CCA)\s+LEXIS\s+\d+\b", re.I)


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    cite: str = ""
    detail: str = ""

    @property
    def cell_value(self) -> str:
        return self.cite if self.status == EXTRACTED else self.status


def classify_saved_result(cite, status="") -> str:
    """Legacy 'Not Available' is ambiguous and cannot prove a negative result."""
    text = str(cite or "").strip()
    if status == NO_RESULTS and text == NO_RESULTS:
        return NO_RESULTS
    if status in {UNVERIFIED, NOT_CHECKED}:
        return status
    if status in {"", EXTRACTED} and CITE_PATTERN.search(text):
        return EXTRACTED
    return UNVERIFIED
