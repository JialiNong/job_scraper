"""
E2E harness: throwaway MongoDB + the real web server + headless Chrome.

Nothing here can reach your real data:
  - mongod runs on a random local port with a temp --dbpath, deleted after
  - the server gets MONGO_URI=127.0.0.1:<that port> and DATABASE_NAME=job_scraper_e2e
    (load_dotenv never overrides env that is already set, so .env is ignored)
  - OPENAI / Telegram keys are blanked, so no AI call or push can happen
  - check_isolated() refuses to run if any of that is not true
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
from bson import ObjectId
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
DB_NAME = "job_scraper_e2e"
USERNAME = "e2e"
PASSWORD = "e2e-password"
AGENT_TOKEN = "e2e-agent-token"

# Fixed ids so tests can address cards directly (#card-<id>)
PENDING_ID = ObjectId("65f000000000000000000001")    # Not Applied, no timeline
APPLIED_ID = ObjectId("65f000000000000000000002")    # Applied yesterday
DELETE_ID = ObjectId("65f000000000000000000003")     # Used by the delete test
UNMATCHED_ID = ObjectId("65f0000000000000000000a1")  # jobs doc, score 4.0
TIMEOUT_ID = ObjectId("65f0000000000000000000b1")    # timeout_jobs doc


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_mongod() -> str | None:
    found = shutil.which("mongod")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/mongod", "/usr/local/bin/mongod"):
        if os.path.exists(candidate):
            return candidate
    return None


def _wait(check, what: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            if check():
                return
        except Exception as exc:  # not up yet
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {what} ({last})")


class Mongo:
    """A mongod process on a temp dir; stop() deletes everything."""

    def __init__(self):
        binary = find_mongod()
        if not binary:
            raise RuntimeError("mongod not found (brew install mongodb/brew/mongodb-community)")
        self.dir = tempfile.mkdtemp(prefix="job_scraper_e2e_mongo_")
        self.port = _free_port()
        self.uri = f"mongodb://127.0.0.1:{self.port}/"
        self.proc = subprocess.Popen(
            [binary, "--dbpath", self.dir, "--port", str(self.port), "--bind_ip", "127.0.0.1",
             "--logpath", os.path.join(self.dir, "mongod.log")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.client = MongoClient(self.uri, serverSelectionTimeoutMS=500)
        _wait(lambda: self.client.admin.command("ping"), "mongod")
        self.db = self.client[DB_NAME]

    def stop(self):
        self.client.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


def _matched(oid, title, company, **extra):
    now = datetime.now()
    doc = {
        "_id": oid, "title": title, "company": company, "location": "Berlin, Germany",
        "link": f"https://www.linkedin.com/jobs/view/{int(str(oid)[-6:], 16) + 4100000000}/",
        "job_id": str(int(str(oid)[-6:], 16) + 4100000000), "source": "linkedin",
        "description": "We build backend services in Node.js. " * 20,
        "match_score": 8.0, "summary": f"{title} summary", "status": "pending",
        "matched_at": now, "applied_at": None, "application_timeline": [],
        "notes": "", "application_qa": [], "highlights": [], "origin": "pipeline",
    }
    doc.update(extra)
    return doc


def seed(db):
    """Reset the e2e database to a known state."""
    for name in db.list_collection_names():
        db.drop_collection(name)
    yesterday = datetime.now() - timedelta(days=1)
    db.matched_jobs.insert_many([
        _matched(PENDING_ID, "Backend Engineer", "Acme", applicants="Be among the first 25 applicants"),
        _matched(APPLIED_ID, "Node.js Developer", "Globex", status="applied", applied_at=yesterday,
                 application_timeline=[{"step": "applied", "at": yesterday}],
                 applicants="47 applicants", applicant_position=12),
        _matched(DELETE_ID, "Platform Engineer", "Initech", applicants="Over 200 applicants"),
    ])
    db.jobs.insert_many([
        {"job_id": "4100000003", "source": "linkedin", "title": "Platform Engineer", "company": "Initech"},
        {"_id": UNMATCHED_ID, "job_id": "4200000001", "source": "linkedin", "title": "Java Engineer",
         "company": "Umbrella", "location": "Munich, Germany",
         "link": "https://www.linkedin.com/jobs/view/4200000001/",
         "description": "Java and Spring required.", "match_score": 4.0,
         "matched_at": datetime.now(), "disqualification_reason": "Java backend required",
         "summary": "Java role", "applicants": "80 applicants"},
    ])
    db.timeout_jobs.insert_one({
        "_id": TIMEOUT_ID, "job_id": "4300000001", "source": "linkedin", "title": "Frontend Engineer",
        "company": "Hooli", "location": "Hamburg, Germany",
        "link": "https://www.linkedin.com/jobs/view/4300000001/",
        "review_status": "open", "timeout_count": 1, "created_at": datetime.now(),
    })


class Server:
    """The real entry point (src/web/web_app.py) as a subprocess."""

    def __init__(self, mongo_uri: str):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {
            **os.environ,
            "MONGO_URI": mongo_uri,
            "DATABASE_NAME": DB_NAME,
            "TRACKER_USERNAME": USERNAME,
            "TRACKER_PASSWORD_HASH": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(4)).decode(),
            "FLASK_SECRET_KEY": "e2e-secret",
            "FLASK_ENV": "development",  # plain-HTTP cookies on localhost
            "AGENT_API_TOKEN": AGENT_TOKEN,
            "OPENAI_API_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
            "PYTHONUNBUFFERED": "1",
        }
        check_isolated(self.env)
        self.log_path = os.path.join(tempfile.gettempdir(), f"job_scraper_e2e_server_{self.port}.log")
        self.log = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [sys.executable, "src/web/web_app.py", "--port", str(self.port)],
            cwd=ROOT, env=self.env, stdout=self.log, stderr=subprocess.STDOUT,
        )

        def up():
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited, see {self.log_path}")
            return urllib.request.urlopen(self.base + "/health", timeout=1).status == 200

        _wait(up, "web server")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def check_isolated(env: dict):
    """Hard stop if the server could touch anything but the throwaway DB."""
    uri = env.get("MONGO_URI", "")
    if not uri.startswith("mongodb://127.0.0.1:"):
        raise RuntimeError(f"E2E refused: MONGO_URI must be the local throwaway mongod, got {uri!r}")
    if not env.get("DATABASE_NAME", "").endswith("_e2e"):
        raise RuntimeError("E2E refused: DATABASE_NAME must end with _e2e")
    if env.get("OPENAI_API_KEY") or env.get("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError("E2E refused: AI / Telegram keys must be blank")


def launch_browser(playwright):
    """Installed Google Chrome (no download); falls back to Playwright's Chromium."""
    try:
        return playwright.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return playwright.chromium.launch(headless=True)
