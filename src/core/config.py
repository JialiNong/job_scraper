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
]

# ===== Scrape budgets =====
# Full run (run_task.sh / Telegram /jobs): last 24 hours.
# Both platforms walk every DEFAULT_KEYWORDS entry.
FULL_MAX_PAGES = 3
INDEED_JOBS_PER_PAGE = 15  # Indeed SERP page size; also the start= step
LINKEDIN_JOBS_PER_PAGE = 30

# Light run (run_quick.sh / Telegram /quick_jobs).
# Indeed's date filter cannot go below 1 day, so the light pass reuses the
# same 24h search and only walks fewer pages per keyword.
# LinkedIn does not loop keywords — it uses LINKEDIN_QUICK_CONFIG (one 12h URL).
LIGHT_INDEED_MAX_PAGES = 2
LIGHT_LINKEDIN_MAX_PAGES = 3

# Ad-hoc LinkedIn (run_linkedin.sh / /linkedin [hours] / --hours N):
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
    r"\bForward\s+Deployed\b",
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
    r"\bFounding(?:\s+\w+){0,4}\s+Engineer\b",  # Founding Engineer / Founding AI Engineer / …
]


# ===== Indeed Platform Configuration =====
INDEED_CONFIG = {
    "source": "indeed",
    "base_url": "https://de.indeed.com/jobs",
    "fromage": "1",  # Posted within: last 1 day (Indeed minimum)
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


# ===== LinkedIn light-run search (12-hour window) =====
# Used by the morning light run. One OR query instead of the keyword loop,
# because the 12h filter lives on this URL (f_TPR=r43200).
LINKEDIN_QUICK_CONFIG = {
    "source": "linkedin",
    # Direct search URL — keywords encode an OR query across all target roles.
    # f_TPR=r43200 → posted within the last 43 200 seconds (12 hours).
    "url": (
        "https://www.linkedin.com/jobs/search-results/"
        "?keywords=full-time%20Full%20Stack%20Engineer%20or%20Frontend%20Developer"
        "%20or%20Product%20Engineer%20or%20Generative%20AI%20Engineer"
        "%2C%20on-site%20or%20hybrid%20or%20remote"
        "&geoId=101282230"
        "&f_TPR=r43200"
        "&origin=JOB_SEARCH_PAGE_JOB_FILTER"
        "&refresh=true"
    ),
    "time_window_hours": 12,

    # SDUI search-results cards. componentkey is job-card-component-ref-<job id>.
    "selectors": {
        "job_card": '[componentkey^="job-card-component-ref-"][role="button"]',
        "next_page": '[data-testid="pagination-controls-next-button-visible"]',
    }
}
