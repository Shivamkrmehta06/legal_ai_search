"""Load everything KMS holds on one judgment, for when a user clicks More."""

from concurrent.futures import ThreadPoolExecutor

from .kms_client import KMSClient
from .kms_router import INFO_TABLES, PAGE_SIZE, PASSAGES, CallLog, _passages


def judgment_details(client: KMSClient, case_id: str) -> dict:
    """Raises KMSClientError if the case itself can't be loaded."""
    log = CallLog(client)
    case = log.get(f"/api/v1/legal-cases/{case_id}/", {})
    with ThreadPoolExecutor(max_workers=len(INFO_TABLES)) as executor:
        tables = list(
            executor.map(
                lambda name: log.list_or_none(INFO_TABLES[name][0], {INFO_TABLES[name][1]: case_id, "page_size": PAGE_SIZE}),
                INFO_TABLES,
            )
        )
    unavailable = [name for name, records in zip(INFO_TABLES, tables) if records is None]
    judgment = {**case, **dict(zip(INFO_TABLES, (records or [] for records in tables)))}
    judgment[PASSAGES] = _passages(log, case_id, unavailable)
    judgment["unavailable_sections"] = unavailable
    judgment["api_calls"] = log.calls
    return judgment
