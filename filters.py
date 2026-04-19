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
    # Core engineering — specific enough, low false positive risk
    "software engineer", "software developer",
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
    "infrastructure engineer", "infrastructure",
    "platform engineer", "platform",
    "cloud engineer", "cloud architect",
    "devops", "dev ops", "dev-ops",
    "sre", "site reliability",
    # Data — specific compound phrases only
    "data engineer",
    "data platform", "data infrastructure",
    # "machine learning", "mlops",
    # "deep learning", "nlp", "llm",
    # AI / ML — word-boundary patterns handle "AI Engineer", "ML Engineer"
    "artificial intelligence",
    # "genai", "gen ai", "generative ai",
    # Security — engineering roles only, not generic "security" or "security manager"
    "security engineer", "security architect",
    # "appsec", "devsecops", "infosec",
    # "penetration", "pentesting",
    # "soc engineer", "siem engineer",
    # "cryptography",
    # "cybersecurity engineer",
    # Architecture — specific
    "software architect", "solutions architect",
    "enterprise architect", "technical architect",
    "cloud architect", "integration architect",
    # Leadership — engineering-specific
    "tech lead", "technical lead",
    "engineering lead", "engineering manager",
    "staff engineer", "principal engineer",
    # Mobile
    # "mobile engineer", "mobile developer",
    # "ios engineer", "ios developer",
    # "android engineer", "android developer",
    # "react native", "flutter",
    # Quality — engineering specific
    "quality engineer", "test engineer", "sdet",
    "automation engineer", "qa engineer",
    # Product/Program — technical lean only
    "technical program manager", "engineering program",
    "product engineer",
    # Networking / Infra — engineering roles only
    "network engineer", "network architect",
    "systems engineer", "systems architect",
    # "embedded engineer", "firmware engineer",
    # "kernel engineer",
    "database engineer", "database architect", "database administrator", "dba",
    # Cloud / tooling — specific enough
    "kubernetes", "docker", "terraform", "AWS", "azure",
    # Tools/domains — specific
    "distributed systems",
    "microservices",
    "fintech engineer", "financial technology",
    "banking technology", "banking engineer",
    "compiler engineer",
    # IT / Support — specific technical roles only
    "information technology",
    "sysadmin", "system administrator",
    "it engineer", "it architect",
    # BI / Data Viz — specific
    # "bi engineer", "business intelligence engineer",
    # "data warehouse engineer", "etl engineer",
    # Research — engineering/science specific
    # "research engineer", "research scientist",
    # "applied scientist", "applied researcher",
]

# Build regex — most keywords use literal match, but AI and ML need
# word boundaries to catch "AI Engineer", "ML Engineer", "AI/ML Engineer"
_KEYWORD_RE = "|".join(re.escape(k) for k in INCLUDE_KEYWORDS)
_INCLUDE_RE = re.compile(
    r"\b(ai|ml)\b|" + _KEYWORD_RE,
    re.IGNORECASE,
)


def is_software_role(title: str, department: str = "") -> bool:
    combined = f"{title} {department}"
    return bool(_INCLUDE_RE.search(combined))