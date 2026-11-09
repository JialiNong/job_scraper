"""
Job Scraper Configuration
Centralized configuration for all scrapers
"""

# ===== Common Configuration =====

# Default search keywords
DEFAULT_KEYWORDS = [
    "frontend engineer",
    "full-stack engineer",
    "fullstack engineer",
    "product engineer",
    "software engineer",
    "forward deployed engineer",
]

# Titles containing one of these are clicked without the AI title check.
# "software engineer" is a search keyword but NOT here: it also matches
# embedded / HMI / data titles ("Qt/QML Simulation HMI Software Engineer"),
# so those titles go through the AI title check instead.
TITLE_AUTO_PASS_KEYWORDS = [
    "frontend engineer",
    "full-stack engineer",
    "fullstack engineer",
    "product engineer",
    "forward deployed engineer",
]

# ===== Scrape budgets =====
# scripts/run_jobs.sh / Telegram /jobs: default last 24 hours.
# Both platforms walk every DEFAULT_KEYWORDS entry.
# FULL_MAX_PAGES is the default pages per keyword (/jobs ... [pages] overrides it).
FULL_MAX_PAGES = 3
INDEED_JOBS_PER_PAGE = 15  # Indeed SERP page size; also the start= step
LINKEDIN_JOBS_PER_PAGE = 30

# LinkedIn --hours N (CLI runs without -p):
# f_TPR still maps hours → seconds; only the default page budget changes.
# Window strictly under 24h → fewer pages per keyword (less volume).
# Explicit -p / --max-pages always wins.
LINKEDIN_SHORT_WINDOW_MAX_PAGES = 2


def linkedin_default_max_pages(hours=None) -> int:
    """Pages per keyword for LinkedIn keyword-loop scrapes (when -p omitted)."""
    if hours is not None and float(hours) < 24:
        return LINKEDIN_SHORT_WINDOW_MAX_PAGES
    return FULL_MAX_PAGES


# Defaults for a manual full-shaped CLI run (no -p / -j).
DEFAULT_MAX_PAGES = FULL_MAX_PAGES
# Alias used by LinkedIn card processing. Indeed uses INDEED_JOBS_PER_PAGE.
MAX_JOBS_PER_PAGE = LINKEDIN_JOBS_PER_PAGE

# Chrome debugging configuration
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
CDP_URL = f"http://{CDP_HOST}:{CDP_PORT}"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_USER_DATA_DIR = "/tmp/chrome_selenium"


# ===== Title Pre-filter (skip clicking if title matches any pattern) =====
# Patterns are case-insensitive regex. Use \b for word boundaries where needed.
# Java uses \b to avoid matching JavaScript.
TITLE_EXCLUDE_KEYWORDS = [
    r"\bJava\b",           # Java (not JavaScript)
    # Cloud *role*, not "Cloud" as a product/company adjective
    # (e.g. keep "Frontend Engineer – Cloud SaaS")
    r"\bCloud\s+(Engineer|Architect|Consultant|Native|Operations|Platform|Infrastructure)\b",
    r"\bLead\b",
    r"\bStaff\b",
    r"\bDevOps\b",
    r"C#",                 # C# / C++/C#
    r"\.NET",              # .NET
    r"\bEmbedded\b",
    r"C\+\+",              # C++
    r"\bQA\b",             # QA Engineer
    r"\bTest\s+Engineer\b",
    r"\bProject\s+Manager\b",
    r"\bFlutter\b",
    r"\bReact\s+Native\b",
    r"\bPrincipal\b",
    r"\bManager\b",        # also catches Project Manager
    r"\bAndroid\b",
    r"\bArchitect\b",
    r"\bData\s+Scientist\b",
    r"\bData\s+Engineer\b",
    r"\bData\s+Analyst\b",
    r"\bQuality\s+Engineer\b",
    r"\bDesign\s+Engineer\b",
    r"\biOS\b",
    r"\bSwift\b",
    r"\bInfrastructure\s+Engineer\b",
    r"\bAnalytics\s+Engineer\b",
    r"\bKubernetes\b",
    r"\bLinux\b",
    r"\bApplied\s+Researcher\b",
    r"\bApplied\s+AI\s+Engineer\b",
    r"\bAI\s+Engineer\b",
    r"\bAI\s+Training\b",
    r"\bLLM(?:\s+\w+){0,3}\s+Engineer\b",
    r"\bGenerative\s+AI\b",
    r"\bPrompt\s+Engineer\b",
    r"\bNLP\s+Engineer\b",
    r"\bMachine\s+Learning\b",
    r"\bPHP\b",
    r"\bPython\b",
    r"\bRobot\s+Learning\s+Engineer\b",
    r"\bCAD\s+Engineer\b",
    r"\bElectronic\s+Design\b",
    r"\bResearcher\b",
    r"\bTechnical\s+Customer\s+Support\b",
    r"\bHead\s+of\b",
    r"\bSecurity(?:\s+\w+){0,4}\s+Engineer\b",  # Security Engineer / Security Software Engineer / …
    r"\bRobotic\b",        # Robotic / Robotics
    r"\bRobotics\b",
    r"\bSystems?\s+Engineer\b",  # System / Systems Engineer
    r"\bProcess\s+Validation\s+Engineer\b",
    r"\bSafety\s+Engineer\b",
    r"\bUI\b",             # UI Designer / UI Engineer
    r"\bUX\b",             # UX Designer / UX Researcher
    r"\bUI/?UX\b",
    r"\bManufacturing\b",
    r"\bLocalization\s+Engineer\b",
    r"\bExecutive\b",
    r"\bMLOps\b",
    r"\bSales\s+Engineer\b",
    r"\bCustomer\s+Engineer\b",
    r"\bQuality\s+Management\b",
    r"\bDeep\s+Learning\b",
    r"\bSupport\s+Engineer\b",
    r"\bSolutions?\s+Engineer\b",
    r"\bR&D\s+Engineer\b",
    r"\bSolutions?\s+Consultant\b",
    r"\bDSP\s+Engineer\b",
    r"\bResearch\s+Engineer\b",
    r"\bML\s+Engineer\b",
    r"\bNDE\s+Engineer\b",
    r"\bMechanical\s+Engineer\b",
    r"\bWorking\s+Student\b",
    r"\bIntern\b",
    r"\bBackend\b",
    r"\bFounding(?:\s+\w+){0,4}\s+Engineer\b",  # Founding Engineer / Founding AI Engineer / …
]


# ===== Match-stage company blocklist =====
# Aggregators / job boards that often appear as the scraped "company".
# Still scored on `jobs`, but never written to `matched_jobs`.
MATCH_BLOCKED_COMPANIES = [
    "instaffo",
    "jobgether",
    "Alignerr",
    "Hyra",
    "The Ai Training Company",
    "TaskVerse",
]

# ===== Indeed Platform Configuration =====
INDEED_CONFIG = {
    "source": "indeed",
    "base_url": "https://de.indeed.com/jobs",
    "fromage": "1",  # Posted within: last 1 day (Indeed minimum)
    "fromage_choices": (1, 3, 7, 14),  # Only windows Indeed's date filter offers
    "location": "",  # empty — same as a desktop SERP with no city typed
    "sort": "",  # Indeed default (relevance); add "date" only if we want newest-first
    "results_per_page": INDEED_JOBS_PER_PAGE,
    
    # Selectors
    "selectors": {
        "job_card": ".mainContentTable",
        "job_title": "h3.jobTitle",
        "detail": '[data-testid="viewjob-main-content"]',
    }
}


# ===== LinkedIn Platform Configuration =====
LINKEDIN_CONFIG = {
    "source": "linkedin",
    "base_url": "https://www.linkedin.com/jobs/search/",
    "geo_id": "101282230",  # Germany
    "time_filter": "r86400",  # Last 24 hours

    # Selectors
    "selectors": {
        "job_card": ".scaffold-layout__list-item",
        "job_link": "a.job-card-container__link",
    }
}


# ===== Match score weights (src/matching/evaluator.py) =====
# The model only labels the job (docs/matching_rules.md) and the company; this table
# turns labels into a 0–10 score. Tune preferences here instead of in prompts.
MATCH_WEIGHTS = {
    "hard_fail_score": 1.0,
    # Base score by what the role actually is
    "role_type": {
        "frontend": 7.0,
        "fullstack_frontend_leaning": 6.5,
        "fullstack_balanced": 5.5,
        "fullstack_backend_leaning": 3.5,
        "backend": 2.0,
        "not_web_dev": 0.0,
    },
    "stack_fit": {"strong": 1.0, "partial": 0.0, "weak": -2.0},
    "seniority": {"junior": -0.5, "mid": 0.0, "senior": 0.0, "lead_or_above": -3.0},
    "backend_language_ask": {"implied_non_js": -1.0},  # required_non_js is a hard fail
    "ai_product_work": 0.5,
    "preferred_location": 0.3,  # Leipzig / Halle / Berlin / remote
    # Company (layer 2): business type and where its customers are
    "product_type": {
        "b2b_saas": 1.0,
        "b2c_ecommerce_marketplace": 1.0,
        "consumer_app": 0.5,
        "fintech": 0.3,
        "other_software_product": 0.0,
        "agency_consulting_outsourcing": -1.0,
        "staffing_recruiter": -1.0,
        "hardware_industrial_automotive": -2.5,
        "public_sector_research": -0.5,
        "other": -0.5,
    },
    "recruiter_posting": -1.0,  # agency posting on behalf of another employer
    "markets": {
        "global": 0.7,
        "europe": 0.4,
        "dach": 0.0,
        "germany_only": -0.5,
        "unclear": 0.0,
    },
}
