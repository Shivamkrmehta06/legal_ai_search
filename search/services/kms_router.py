"""Call only the KMS APIs a question needs, chosen by fixed rules.

The AI model says what was asked. These rules decide which API answers it, so a
model's mistake can't trigger an unexpected call.
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import re

from .kms_client import KMSClient, KMSClientError

logger = logging.getLogger(__name__)

MAX_WORKERS = 16
PAGE_SIZE = 100
# Above this many cases, requested details are left for the user to open with More.
MAX_CASES_WITH_DETAILS = 10
MAX_RETRY_TERMS = 3

# What can be asked about a case -> (API, the field holding the case id).
INFO_TABLES = {
    "judges": ("/api/v1/case-judges/", "case"),
    "parties": ("/api/v1/case-parties/", "case"),
    "statutes": ("/api/v1/case-statute-links/", "case"),
    "issues": ("/api/v1/case-issue-links/", "case"),
    "domains": ("/api/v1/case-domain-links/", "case"),
    "propositions": ("/api/v1/legal-propositions/", "case"),
    "cases_cited": ("/api/v1/case-citations/", "citing_case"),
    "cited_by": ("/api/v1/case-citations/", "cited_case"),
    "citation_aliases": ("/api/v1/citation-aliases/", "case"),
    "legal_documents": ("/api/v1/legal-documents/", "case"),
    "case_documents": ("/api/v1/case-documents/", "case"),
    "versions": ("/api/v1/case-versions/", "case"),
    "metadata_versions": ("/api/v1/case-metadata-versions/", "case"),
}
# What can be asked about a case that the legal-cases API answers itself.
CASE_FIELDS = {
    "summary": ["summary"],
    "court": ["court_name"],
    "judgment_date": ["judgment_date"],
    "case_number": ["case_number"],
    "citation": ["neutral_citation", "primary_citation"],
    "outcome": ["disposition"],
    "bench": ["bench_details"],
}
PASSAGES = "passages"
ANSWERABLE = {*INFO_TABLES, *CASE_FIELDS, PASSAGES}

# Words too common in legal questions to say what a question is about.
COMMON_WORDS = frozenset(
    """a about act acts an and any are as at by case cases court courts decision decisions for from
    has have high in is it judgment judgments judgement judgements law laws legal of on or section
    sections supreme that the to under what when where which with order orders ruling rulings
    precedent precedents previous past related regarding show give tell find provide please
    mujhe bare mein batao bata dikhao chahiye hai kya kaise par hone milti aur faisla faisle nirnay wale wala""".split()
)


class CallLog:
    """Run KMS calls and remember them, so the response can show exactly what was called."""

    def __init__(self, client: KMSClient):
        self.client = client
        self.calls = []

    def get(self, endpoint: str, params: dict):
        entry = {"endpoint": endpoint, "params": params, "ok": False}
        self.calls.append(entry)
        payload = self.client.get(endpoint, params)
        entry["ok"] = True
        return payload

    def list(self, endpoint: str, params: dict) -> list[dict]:
        results = KMSClient._extract_results(self.get(endpoint, params))
        return [record for record in results if isinstance(record, dict)]

    def list_or_none(self, endpoint: str, params: dict) -> list[dict] | None:
        """Return None if the API failed, so a failure isn't mistaken for an empty answer."""
        try:
            return self.list(endpoint, params)
        except KMSClientError as exc:
            logger.warning("KMS %s failed: %s", endpoint, exc)
            return None


def answer_query(client: KMSClient, query: str, analysis: dict) -> dict:
    """Find the cases the question is about, then fetch only what it asked for."""
    log = CallLog(client)
    requested = [item for item in analysis.get("requested_info") or [] if item in ANSWERABLE]
    act, section = act_and_section(analysis.get("act"), analysis.get("section"), query)
    analysis = {**analysis, "act": act, "section": section}
    try:
        cases, problem = _find_cases(log, query, analysis, requested)
    except KMSClientError as exc:
        logger.warning("KMS search failed: %s", exc)
        return {
            "judgments": [],
            "judgment_count": 0,
            "requested_info": requested,
            "response_message": None,
            "results_title": None,
            "understood_law": {"act": act, "section": section},
            "retrieval": _retrieval(
                log,
                unavailable=True,
                message="The legal database could not be reached, so no records could be loaded. Please try again shortly.",
            ),
        }

    judgments = [_heading(case) for case in cases]
    if requested and len(judgments) <= MAX_CASES_WITH_DETAILS:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            judgments = list(executor.map(lambda judgment: _add_requested(log, judgment, requested), judgments))
    return {
        "judgments": judgments,
        "judgment_count": len(judgments),
        "requested_info": requested,
        "response_message": problem or describe_answer(analysis, query, judgments, requested),
        "results_title": results_title(analysis, query, judgments, requested),
        "understood_law": {"act": analysis["act"], "section": section},
        "retrieval": _retrieval(log),
    }


def results_title(analysis: dict, query: str, judgments: list, requested: list) -> str | None:
    """Name the results after what was asked. With nothing to show, the message says it all."""
    if not judgments:
        return None
    if requested and len(judgments) == 1:
        return "Case details"
    if analysis.get("case_name"):
        return "Matching cases"
    subject = _subject(analysis, query)
    return f"Judgments on {subject}" if subject else "Related judgments"


def describe_answer(analysis: dict, query: str, judgments: list, requested: list) -> str:
    """Say what was found, and what this service is for when asked for something else."""
    count = len(judgments)
    plural = "" if count == 1 else "s"
    if not count:
        found = "We could not find a matching judgment for this question."
        hint = "Try naming a case, an Act or a section, for example: judgments on Section 138 of the NI Act."
        return f"{found} {hint}"
    subject = _subject(analysis, query)
    about = f" related to {subject}" if subject else ""
    unmatched = analysis.get("unmatched_act")
    notice = f'We could not match "{unmatched}" to an Act in our records. ' if unmatched else ""
    if analysis.get("request_type") == "legal_question":
        return f"{notice}We provide judgments rather than legal advice. Here {'is' if count == 1 else 'are'} {count} judgment{plural}{about}."
    if requested and count > MAX_CASES_WITH_DETAILS:
        return f"{notice}Found {count} judgments{about}. Click More on a judgment to see its details."
    if requested and count == 1:
        return f"{notice}Here is what you asked about {judgments[0]['title']}."
    return f"{notice}Found {count} judgment{plural}{about}."


def _find_cases(log: CallLog, query: str, analysis: dict, requested: list) -> tuple[list[dict], str | None]:
    """Apply the selection rules in order. Returns the cases, and a message if there's a problem."""
    case_name = analysis.get("case_name")
    act = analysis.get("act")
    section = analysis.get("section")

    if case_name:
        return _cases_by_name(log, case_name)
    if act or section:
        return _cases_by_law(log, query, analysis, act, section), None
    if requested and not analysis.get("court") and not analysis.get("legal_issues"):
        return [], "Please name the case you are asking about, for example: who were the judges in ABC Traders v XYZ Enterprises?"
    return _cases_by_topic(log, query, analysis), None


def _cases_by_name(log: CallLog, name: str) -> tuple[list[dict], str | None]:
    words = [word for word in re.findall(r"\w{3,}", name.lower()) if word not in {"and", "the", "ors", "anr", "others"}]
    for term in dict.fromkeys([name, *words[:MAX_RETRY_TERMS]]):
        cases = log.list("/api/v1/legal-cases/", {"search": term, "page_size": PAGE_SIZE})
        if cases:
            if len(cases) > 1:
                return cases, f'Found {len(cases)} cases matching "{name}". Click More on the one you mean.'
            return cases, None
    return [], f'We could not find a case matching "{name}". Please check the case name.'


def _cases_by_law(log: CallLog, query: str, analysis: dict, act: str | None, section: str | None) -> list[dict]:
    """Follow KMS's own links from an Act or section to the cases that discuss it.

    Updates analysis with the Act that was really found, so the page can't name another.
    """
    statute = find_record(log, "/api/v1/statutes/", act) if act else None
    if statute:
        analysis["act"] = statute.get("official_name") or act
    links = []
    found_acts = set()
    if section:
        params = {"search": section, "page_size": PAGE_SIZE}
        if statute:
            params["statute"] = statute.get("id")
        for provision in log.list("/api/v1/statutory-provisions/", params):
            if str(provision.get("provision_code")).lower() == section.lower():
                found_acts.add(provision.get("statute_official_name"))
                links += log.list("/api/v1/case-statute-links/", {"provision": provision["id"], "page_size": PAGE_SIZE})
    elif statute:
        links = log.list("/api/v1/case-statute-links/", {"search": statute.get("short_name"), "page_size": PAGE_SIZE})

    if act and not statute and links:
        analysis["unmatched_act"] = act
        analysis["act"] = next(iter(found_acts)) if len(found_acts) == 1 else None

    cases = {link["case"]: {"id": link["case"], "canonical_title": link.get("case_title")} for link in links if link.get("case")}
    if not cases:
        return []

    court = find_record(log, "/api/v1/courts/", analysis["court"]) if analysis.get("court") else None
    if court:
        in_court = log.list("/api/v1/legal-cases/", {"court": court.get("id"), "page_size": PAGE_SIZE})
        cases = {case_id: case for case_id, case in cases.items() if case_id in {item.get("id") for item in in_court}}

    # "director liability under the NI Act" is narrower than the Act alone.
    act_words = set(content_words(f"{act or ''} {analysis.get('section') or ''}"))
    topic_words = [word for word in content_words(query) if word not in act_words][:MAX_RETRY_TERMS]
    for word in topic_words:
        matching = {item.get("id") for item in log.list("/api/v1/legal-cases/", {"search": word, "page_size": PAGE_SIZE})}
        narrowed = {case_id: case for case_id, case in cases.items() if case_id in matching}
        if narrowed:
            return list(narrowed.values())
    return list(cases.values())


def _cases_by_topic(log: CallLog, query: str, analysis: dict) -> list[dict]:
    params = {"page_size": PAGE_SIZE}
    court = find_record(log, "/api/v1/courts/", analysis["court"]) if analysis.get("court") else None
    if court:
        params["court"] = court.get("id")
    issue = next(iter(analysis.get("legal_issues") or []), None)
    # Only the user's own words after that: a model's phrasing can match unrelated records.
    terms = [term for term in [issue, *content_words(query)[:MAX_RETRY_TERMS]] if term]
    if not terms and court:
        return log.list("/api/v1/legal-cases/", params)
    for term in terms:
        cases = log.list("/api/v1/legal-cases/", {**params, "search": term})
        if cases:
            return cases
    return []


def _add_requested(log: CallLog, judgment: dict, requested: list) -> dict:
    """Fetch only what was asked for this case."""
    case_id = judgment["id"]
    unavailable = []
    details = {"requested": requested}

    fields = [item for item in requested if item in CASE_FIELDS]
    if fields:
        try:
            case = log.get(f"/api/v1/legal-cases/{case_id}/", {})
            for item in fields:
                for field in CASE_FIELDS[item]:
                    details[field] = case.get(field)
        except KMSClientError:
            unavailable += fields

    tables = [item for item in requested if item in INFO_TABLES]
    for item in tables:
        endpoint, case_field = INFO_TABLES[item]
        records = log.list_or_none(endpoint, {case_field: case_id, "page_size": PAGE_SIZE})
        if records is None:
            unavailable.append(item)
        details[item] = records or []

    if PASSAGES in requested:
        details[PASSAGES] = _passages(log, case_id, unavailable)

    return {**judgment, **details, "unavailable_sections": unavailable}


def _passages(log: CallLog, case_id: str, unavailable: list) -> list[dict]:
    documents = log.list_or_none("/api/v1/legal-documents/", {"case": case_id, "page_size": PAGE_SIZE})
    if documents is None:
        unavailable.append(PASSAGES)
        return []
    passages = []
    for document in documents:
        records = log.list_or_none("/api/v1/case-paragraphs/", {"document__legal_document": document["id"], "page_size": PAGE_SIZE})
        if records is None:
            unavailable.append(PASSAGES)
        passages += records or []
    return passages


def _heading(case: dict) -> dict:
    title = case.get("canonical_title") or case.get("original_title") or case.get("case_number") or "Untitled judgment"
    return {"id": case["id"], "title": title}


def _retrieval(log: CallLog, unavailable=False, message=None) -> dict:
    retrieval = {"is_mock": False, "message": message, "api_calls": log.calls, "results": []}
    if unavailable:
        retrieval["unavailable"] = True
    return retrieval


def find_record(log, endpoint: str, term: str) -> dict | None:
    """Turn a name into the record KMS filters need. If unsure, the filter is left out."""
    results = KMSClient._extract_results(log.get(endpoint, {"search": term, "page_size": 1}))
    return results[0] if results and isinstance(results[0], dict) else None


def act_and_section(act: str | None, section: str | None, query: str) -> tuple[str | None, str | None]:
    """Some models put the section in the Act's name, as in "NE Act 138". Years stay in the name."""
    section = mentioned_section(section, query)
    act = _western_digits(act or "")
    for number in re.findall(r"(?<!\w)\d+[A-Za-z]*(?!\w)", act):
        if not re.fullmatch(r"\d{4}", number) and mentioned_section(number, query):
            section = section or number
            act = re.sub(rf"(?<!\w){re.escape(number)}(?!\w)", "", act)
    return act.strip(" ,.-") or None, section


def mentioned_section(section: str | None, query: str) -> str | None:
    """Models can infer a section the user never mentioned, which would mislead the search."""
    match = re.search(r"\d+[A-Za-z]*", _western_digits(section or ""))
    number = match.group(0) if match else None
    if number and re.search(rf"(?<![\w]){re.escape(number)}(?![\w])", _western_digits(query), flags=re.IGNORECASE):
        return number
    return None


def _western_digits(text: str) -> str:
    """Treat ١٣٨ and १३८ as 138."""
    return re.sub(r"\d", lambda match: str(int(match.group(0))), text)


def content_words(text: str) -> list[str]:
    words = re.findall(r"\w{3,}", text.lower())
    return list(dict.fromkeys(word for word in words if word not in COMMON_WORDS and not word.isdigit()))


def _subject(analysis: dict, query: str) -> str:
    section = analysis.get("section")
    parts = [f"Section {section}" if section else None, analysis.get("act")]
    return " of the ".join(part for part in parts if part)
