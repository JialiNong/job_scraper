"""HTML pages, health check, and the dev template auto-reload token."""
import os

from flask import Blueprint, current_app, jsonify, render_template
from flask_login import login_required

bp = Blueprint("pages", __name__)


@bp.route("/health")
def health():
    """Health check endpoint for Render and other platforms."""
    return jsonify({"status": "ok", "service": "job-tracker"}), 200


@bp.route("/")
@login_required
def index():
    return render_template("index.html")


@bp.route("/unmatched")
@login_required
def unmatched():
    return render_template("unmatched.html")


@bp.route("/timeouts")
@login_required
def timeouts():
    return render_template("timeouts.html")


@bp.route("/manual-apply")
@login_required
def manual_apply_page():
    return render_template("manual_apply.html")


@bp.route("/api/dev/reload-token")
def api_dev_reload_token():
    """Return latest mtime of HTML templates so the browser can auto-refresh."""
    templates_dir = current_app.template_folder or ""
    latest = 0.0
    try:
        for name in os.listdir(templates_dir):
            if not name.endswith((".html", ".htm")):
                continue
            path = os.path.join(templates_dir, name)
            try:
                latest = max(latest, os.path.getmtime(path))
            except OSError:
                continue
    except OSError:
        pass
    return jsonify({"token": str(int(latest * 1000))})
