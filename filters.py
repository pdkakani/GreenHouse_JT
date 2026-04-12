"""
filters.py — Location and title/department filtering logic.

No server-side filtering exists on the Greenhouse public API.
Everything is done client-side here.
"""

import re

# ---------------------------------------------------------------------------
# Location filtering
# ---------------------------------------------------------------------------

# Patterns that confirm a job is USA-based or USA-remote.
# Checked case-insensitively against the location string.
USA_INCLUDE_PATTERNS = [
    r"\busa\b",
    r"\bus\b",
    r"united states",
    r"u\.s\.a",
    r"u\.s\.",
    r"\bremote\b",          # treat bare "Remote" as potentially US-remote
    r"remote.*us",
    r"us.*remote",
    r"anywhere in the us",
    r"work from anywhere",  # typically US-only for US companies
    # common state names / abbreviations
    r"\bca\b", r"california",
    r"\bny\b", r"new york",
    r"\btx\b", r"texas",
    r"\bwa\b", r"washington",
    r"\bil\b", r"illinois",
    r"\bco\b", r"colorado",
    r"\bma\b", r"massachusetts",
    r"\bga\b", r"georgia",
    r"\bfl\b", r"florida",
    r"\bva\b", r"virginia",
    r"\bor\b", r"oregon",
    r"\baz\b", r"arizona",
    r"\bnc\b", r"north carolina",
    r"\bmn\b", r"minnesota",
    r"\boh\b", r"ohio",
    r"\bmi\b", r"michigan",
    r"\butah\b", r"\but\b",
    r"\bnv\b", r"nevada",
    r"san francisco", r"new york city", r"nyc", r"chicago",
    r"seattle", r"austin", r"boston", r"denver", r"atlanta",
    r"los angeles", r"\bla\b", r"miami", r"portland",
]

# Patterns that explicitly indicate a non-US location → exclude.
NON_USA_PATTERNS = [
    r"\buk\b", r"united kingdom", r"england", r"london",
    r"\beu\b", r"europe", r"european union",
    r"canada",  # \bca\b removed — "CA" alone is also California; canada word is sufficient
    r"australia", r"\bau\b",
    r"india",  # \bin\b removed — matches preposition "in" (e.g. "Remote in USA")
    r"germany", r"berlin", r"munich",
    r"france", r"paris",
    r"netherlands", r"amsterdam",
    r"singapore", r"hong kong",
    r"japan", r"tokyo",
    r"brazil",
    r"mexico",
    r"ireland", r"dublin",
    r"poland", r"warsaw",
    r"sweden", r"stockholm",
    r"spain", r"madrid",
    r"israel", r"tel aviv",
]

_USA_RE = re.compile("|".join(USA_INCLUDE_PATTERNS), re.IGNORECASE)
_NON_USA_RE = re.compile("|".join(NON_USA_PATTERNS), re.IGNORECASE)


def is_usa_location(location: str) -> bool:
    """
    Returns True if the job location is USA or USA-remote.
    An empty location is treated as possibly remote → included.
    """
    if not location or location.strip() == "":
        return True  # blank = assume remote/unspecified → include

    loc = location.strip()

    # If explicitly non-USA, reject first
    if _NON_USA_RE.search(loc):
        # But if it ALSO contains USA patterns (e.g. "US & UK"), keep it
        if _USA_RE.search(loc):
            return True
        return False

    # If USA pattern found, include
    if _USA_RE.search(loc):
        return True

    # Unknown location → exclude to keep signal clean
    return False


# ---------------------------------------------------------------------------
# Title / department filtering — SOFTWARE & IT INCLUSION
# ---------------------------------------------------------------------------

# Broad allowlist: any job whose title OR department matches any of these
# passes through. Deliberately generous to avoid missing edge-case postings.
INCLUDE_KEYWORDS = [
    # Core engineering
    "engineer", "engineering",
    "developer", "development",
    "programmer", "programming",
    "coder",
    # Roles
    "backend", "back-end", "back end",
    # "frontend", "front-end", "front end",
    # "fullstack", "full-stack", "full stack",
    "software",
    "infrastructure",
    "platform",
    "cloud",
    "devops", "dev ops", "dev-ops",
    "sre", "site reliability",
    "systems",
    # Data
    # "data engineer", "data scientist", "data analyst",
    # "analytics", "analyst",
    # "machine learning", " ml ", "mlops",
    # "artificial intelligence", " ai ",
    # "deep learning",
    # "nlp",
    # "llm",
    # Security
    # "security", "appsec", "devsecops", "infosec",
    # "penetration", "pentesting",
    # "soc analyst", "siem",
    # "cryptography",
    # Architecture
    "architect",
    "solutions architect",
    "technical",
    # Mobile
    # "mobile", "ios", "android",
    # "react native", "flutter",
    # Quality
    # "qa ", " qa", "quality assurance",
    # "test engineer", "sdet", "automation engineer",
    # Product/Program (technical lean)
    "product manager", "technical program", "engineering manager",
    "scrum master", "agile coach",
    # Networking / Infra
    # "network", "networking",
    # "embedded", "firmware",
    # "kernel", "driver",
    # "database", " dba",
    # "database administrator",
    # Cloud / tooling
    "kubernetes", "docker", "terraform",
    "aws", "gcp", "azure",
    # Specific tools/domains
    "api", "saas", "paas",
    "distributed systems",
    "microservices",
    "blockchain",
    "fintech",
    "compiler",
    "operating system",
    # IT / Support (technical)
    " it ", "information technology",
    # "sysadmin", "system administrator",
    # "helpdesk", "help desk",
    # "technical support",
    # BI / Data Viz
    # "bi engineer", "business intelligence",
    # "data warehouse", "etl",
    # Research
    # "research engineer", "research scientist",
    # "applied scientist",
]

# Compile once — match against title + department combined
_INCLUDE_RE = re.compile(
    "|".join(re.escape(k) for k in INCLUDE_KEYWORDS),
    re.IGNORECASE,
)


def is_software_role(title: str, department: str = "") -> bool:
    """
    Returns True if the job title or department suggests a software/IT role.
    """
    combined = f"{title} {department}"
    return bool(_INCLUDE_RE.search(combined))