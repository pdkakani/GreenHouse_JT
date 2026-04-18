"""
filters.py — Location and title/department filtering logic.

Key design principle:
- NON_USA check always wins — if a non-US country is detected, reject
- bare "Remote" with no country context = include (US companies default)
- "Remote" + non-US country = reject
- Unknown location with no signals = exclude (conservative)
"""

import re

# ---------------------------------------------------------------------------
# Location filtering
# ---------------------------------------------------------------------------

# Explicit USA signals — state names, cities, US-specific phrases
# NOTE: bare "Remote" is NOT here — handled separately below
USA_EXPLICIT_PATTERNS = [
    r"\busa\b",
    r"\bu\.s\.a\.?\b",
    r"\bu\.s\.\b",
    r"united states",
    r"remote.*\bus\b",
    r"\bus\b.*remote",
    r"remote.*united states",
    r"united states.*remote",
    r"anywhere in the us",
    r"us.{0,5}only",
    r"work from anywhere",       # US companies use this for US-only roles
    # US states
    r"\bcalifornia\b", r"\bca\b",
    r"\bnew york\b",   r"\bny\b",
    r"\btexas\b",      r"\btx\b",
    r"\bwashington\b", r"\bwa\b",
    r"\billinois\b",   r"\bil\b",
    r"\bcolorado\b",   r"\bco\b",
    r"\bmassachusetts\b", r"\bma\b",
    r"\bgeorgia\b",    r"\bga\b",
    r"\bflorida\b",    r"\bfl\b",
    r"\bvirginia\b",   r"\bva\b",
    r"\boregon\b",     r"\bor\b",
    r"\barizona\b",    r"\baz\b",
    r"\bnorth carolina\b", r"\bnc\b",
    r"\bminnesota\b",  r"\bmn\b",
    r"\bohio\b",       r"\boh\b",
    r"\bmichigan\b",   r"\bmi\b",
    r"\butah\b",       r"\but\b",
    r"\bnevada\b",     r"\bnv\b",
    r"\bnew jersey\b", r"\bnj\b",
    r"\bpennsylvania\b", r"\bpa\b",
    r"\bmaryland\b",   r"\bmd\b",
    r"\btennesee\b",   r"\btn\b",
    r"\bwisconsin\b",  r"\bwi\b",
    # US cities
    r"\bsan francisco\b", r"\bsf\b",
    r"\bnew york city\b", r"\bnyc\b",
    r"\bchicago\b",
    r"\bseattle\b",
    r"\baustin\b",
    r"\bboston\b",
    r"\bdenver\b",
    r"\batlanta\b",
    r"\blos angeles\b",
    r"\bmiami\b",
    r"\bportland\b",
    r"\bsan jose\b",
    r"\bsan diego\b",
    r"\bphoenix\b",
    r"\bdallas\b",
    r"\bhouston\b",
    r"\bnashville\b",
    r"\bminneapolis\b",
    r"\bpittsburgh\b",
    r"\bphiladelphia\b",
    r"\bwashington dc\b", r"\bdc\b",
    r"\braleigh\b",
    r"\bsalt lake city\b",
]

# Non-US locations — if any of these match, the job is excluded
# unless a strong USA signal is also present
NON_USA_PATTERNS = [
    # UK / Ireland
    r"\buk\b", r"united kingdom", r"\bengland\b", r"\bscotland\b", r"\bwales\b",
    r"\blondon\b", r"\bmanchester\b", r"\bedinburgh\b",
    r"\bireland\b", r"\bdublin\b",
    # Europe
    r"\beu\b", r"\beurope\b", r"european union",
    r"\bgermany\b", r"\bberlin\b", r"\bmunich\b", r"\bfrankfurt\b",
    r"\bfrance\b", r"\bparis\b",
    r"\bnetherlands\b", r"\bamsterdam\b",
    r"\bsweden\b", r"\bstockholm\b",
    r"\bspain\b", r"\bmadrid\b", r"\bbarcelona\b",
    r"\bpoland\b", r"\bwarsaw\b",
    r"\bportugal\b", r"\blisbon\b",
    r"\bdenmark\b", r"\bcopenhagen\b",
    r"\bfinland\b", r"\bhelsinki\b",
    r"\bnorway\b", r"\boslo\b",
    r"\bswitzerland\b", r"\bzurich\b",
    r"\baustria\b", r"\bvienna\b",
    r"\bbelgium\b", r"\bbrussels\b",
    r"\bczechia\b", r"\bprague\b",
    r"\bhungary\b", r"\bbudapest\b",
    r"\bromania\b", r"\bbucharest\b",
    # Asia Pacific
    r"\bindia\b", r"\bbangalore\b", r"\bmumbai\b", r"\bhyderabad\b", r"\bpune\b",
    r"\bsingapore\b",
    r"\bhong kong\b",
    r"\bjapan\b", r"\btokyo\b",
    r"\bchina\b", r"\bbeijing\b", r"\bshanghai\b",
    r"\baustralia\b", r"\bsydney\b", r"\bmelbourne\b",
    r"\bnew zealand\b",
    r"\bkorea\b", r"\bseoul\b",
    # Americas (non-US)
    r"\bcanada\b", r"\btoronto\b", r"\bvancouver\b", r"\bmontreal\b",
    r"\bbrazil\b", r"\bsao paulo\b",
    r"\bmexico\b", r"\bmexico city\b",
    r"\bargentina\b", r"\bbuenos aires\b",
    r"\bcolombia\b", r"\bbogota\b",
    # Middle East / Africa
    r"\bisrael\b", r"\btel aviv\b",
    r"\buae\b", r"\bdubai\b",
    r"\bsouth africa\b",
    r"\bnigeria\b",
]

_USA_RE = re.compile("|".join(USA_EXPLICIT_PATTERNS), re.IGNORECASE)
_NON_USA_RE = re.compile("|".join(NON_USA_PATTERNS), re.IGNORECASE)
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)


def is_usa_location(location: str) -> bool:
    """
    Returns True only if the job is clearly USA or USA-remote.

    Rules (in order):
    1. Blank location → include (unspecified = likely remote for US companies)
    2. Non-US country detected → EXCLUDE, even if "Remote" is also present
       Exception: if a strong US signal is ALSO present (e.g. "US & UK roles")
    3. Explicit US signal detected → include
    4. Bare "Remote" with no country context → include
    5. Unknown location with no signals → EXCLUDE
    """
    if not location or location.strip() == "":
        return True

    loc = location.strip()

    has_non_usa = bool(_NON_USA_RE.search(loc))
    has_usa = bool(_USA_RE.search(loc))
    has_remote = bool(_REMOTE_RE.search(loc))

    # Non-US country present
    if has_non_usa:
        # Only keep if there's also an explicit US signal (e.g. "US or UK")
        return has_usa

    # Explicit US signal (state, city, "United States", etc.)
    if has_usa:
        return True

    # Bare "Remote" with no country → include (US companies default)
    if has_remote:
        return True

    # No signals at all → exclude
    return False


# ---------------------------------------------------------------------------
# Quick test (runs when executed directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        ("Remote", True),
        ("Remote - US", True),
        ("Remote, USA", True),
        ("United States", True),
        ("San Francisco, CA", True),
        ("New York, NY", True),
        ("Austin, TX", True),
        ("Remote - Ireland", False),
        ("Remote - UK", False),
        ("Remote - Germany", False),
        ("London, UK", False),
        ("Dublin, Ireland", False),
        ("Berlin, Germany", False),
        ("India", False),
        ("Remote - India", False),
        ("Toronto, Canada", False),
        ("Remote, Europe", False),
        ("", True),
        ("Worldwide", False),
        ("Global", False),
    ]
    print("=== Location filter tests ===")
    all_pass = True
    for loc, expected in tests:
        result = is_usa_location(loc)
        ok = result == expected
        all_pass = all_pass and ok
        print(f"  {'✅' if ok else '❌'} {loc!r:35} → {result} (exp {expected})")
    print(f"\nAll pass: {all_pass}")


# ---------------------------------------------------------------------------
# Title / department filtering — SOFTWARE & IT INCLUSION
# ---------------------------------------------------------------------------

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
    "senior software",
    "principal software",
    "infrastructure",
    "platform",
    "cloud",
    "devops", "dev ops", "dev-ops",
    "sre", "site reliability",
    "systems",
    # Leadership
    "tech lead", "technical lead",
    # Data
    "data engineer", "data scientist", "data analyst",
    "analytics", "analyst",
    # "machine learning", " ml ", "mlops",
    # "artificial intelligence", " ai ",
    # "deep learning",
    # "nlp",
    # "llm",
    # Security
    "security", "appsec", "devsecops", "infosec",
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
    "qa ", " qa", "quality assurance",
    "test engineer", "sdet", "automation engineer",
    # Product/Program (technical lean)
    "product manager", "technical program", "engineering manager",
    "scrum master", "agile coach",
    # Networking / Infra
    "network", "networking",
    # "embedded", "firmware",
    "kernel", "driver",
    "database", " dba",
    "database administrator",
    # Cloud / tooling
    "kubernetes", "docker", "terraform",
    "aws", "gcp", "azure",
    # Specific tools/domains
    "api", "saas", "paas",
    "distributed systems",
    "microservices",
    "blockchain",
    "fintech",
    "financial technology",
    "banking technology",
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

_INCLUDE_RE = re.compile(
    "|".join(re.escape(k) for k in INCLUDE_KEYWORDS),
    re.IGNORECASE,
)


def is_software_role(title: str, department: str = "") -> bool:
    combined = f"{title} {department}"
    return bool(_INCLUDE_RE.search(combined))