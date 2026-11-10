#!/usr/bin/env python3
"""
Job Tracker Web UI
Flask web app to view and manage matched jobs
"""
import sys
import os

# Add src/ to path so package imports work when run as a script
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_SRC_ROOT)
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from dotenv import load_dotenv

# Before the web.* imports: some modules read env at import (MATCH_THRESHOLD)
load_dotenv()

from flask import Flask, request, url_for  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
from core.db_mongo import ensure_indexes  # noqa: E402
from web.auth import bp as auth_bp, login_manager  # noqa: E402
from web.routes import ai_tasks, jobs, log_job, pages, stats, timeouts, unmatched  # noqa: E402

app = Flask(
    __name__,
    template_folder=os.path.join(_PROJECT_ROOT, "templates"),
    static_folder=os.path.join(_PROJECT_ROOT, "static"),
)
# Always pick up HTML/template edits without restarting the server
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Session config (required for login)
_LOCAL_SECRET_FILE = os.path.join(_PROJECT_ROOT, ".flask_secret_key")


def _load_secret_key() -> str:
    """
    FLASK_SECRET_KEY from env (Render), else a key persisted in a gitignored
    local file. A fresh random key per start would invalidate every session
    and remember-me cookie, forcing a new login after each restart.
    """
    key = (os.getenv("FLASK_SECRET_KEY") or "").strip()
    if key:
        return key
    try:
        with open(_LOCAL_SECRET_FILE, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = os.urandom(32).hex()
    try:
        fd = os.open(_LOCAL_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError as exc:
        print(f"[web] Could not persist secret key ({exc}); logins reset on restart")
    return key


app.config["SECRET_KEY"] = _load_secret_key()

# Production proxy support (Render, Railway, etc. use X-Forwarded-* headers)
# This ensures Flask sees the correct scheme (https) and host when behind a reverse proxy
if os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("FLY_APP_NAME"):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Secure session cookies for production (HTTPS)
if not app.debug and not os.getenv("FLASK_ENV") == "development":
    app.config["SESSION_COOKIE_SECURE"] = True  # Only send over HTTPS
    app.config["SESSION_COOKIE_HTTPONLY"] = True  # No JavaScript access
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # CSRF protection

@app.context_processor
def _static_url_helper():
    """
    {{ static_url('js/index.js') }} → /static/js/index.js?v=<mtime>.
    The version changes whenever the file is saved, so browsers never run a
    stale cached copy after an edit or deploy.
    """
    def static_url(filename: str) -> str:
        try:
            version = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            version = 0
        return url_for("static", filename=filename, v=version)
    return {"static_url": static_url}


_default_send_file_max_age = app.get_send_file_max_age


def _send_file_max_age(filename):
    """
    Static files linked through static_url() carry ?v=<mtime>, so a changed
    file always gets a new URL: let browsers keep those for a year instead of
    revalidating every asset on each page load. Anything else keeps Flask's
    default (revalidate).
    """
    if request.endpoint == "static" and request.args.get("v"):
        return 31536000
    return _default_send_file_max_age(filename)


app.get_send_file_max_age = _send_file_max_age


# Authentication setup (single user; see web/auth.py)
login_manager.init_app(app)

# Routes live in Blueprints with no URL prefix, so every path is unchanged.
app.register_blueprint(auth_bp)
for _module in (pages, jobs, unmatched, timeouts, stats, log_job, ai_tasks):
    app.register_blueprint(_module.bp)

# Ensure indexes once at import (Flask reloader may import twice; ensure_indexes is idempotent)
try:
    ensure_indexes()
except Exception as exc:
    print(f"[web] ensure_indexes skipped: {exc}")

# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Job Tracker Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", "-p", type=int, default=5050, help="Port to listen on")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart the server automatically when a .py file changes (local dev)",
    )
    args = parser.parse_args()

    print(f"\n🌐 Job Tracker starting at http://{args.host}:{args.port}")
    if args.reload:
        print("   Auto-reload on: saving a .py file restarts the server.")
    print("   Press Ctrl+C to stop.\n")
    # Reloader only (no interactive debugger) unless --debug is also given.
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=args.reload or args.debug,
    )


if __name__ == "__main__":
    main()
