"""Single-user login (TRACKER_USERNAME + bcrypt TRACKER_PASSWORD_HASH)."""
import os

import bcrypt
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user

bp = Blueprint("auth", __name__)

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access the job tracker."


# Simple User class for single-user auth
class User(UserMixin):
    def __init__(self, user_id):
        self.id = user_id

@login_manager.user_loader
def load_user(user_id):
    username = os.getenv("TRACKER_USERNAME")
    if username and user_id == username:
        return User(user_id)
    return None

def verify_password(username: str, password: str) -> bool:
    """Verify username and password against env vars."""
    expected_username = os.getenv("TRACKER_USERNAME")
    expected_password_hash = os.getenv("TRACKER_PASSWORD_HASH")
    
    if not expected_username or not expected_password_hash:
        print("[AUTH] Warning: TRACKER_USERNAME or TRACKER_PASSWORD_HASH not set!")
        return False
    
    if username != expected_username:
        return False
    
    try:
        return bcrypt.checkpw(password.encode('utf-8'), expected_password_hash.encode('utf-8'))
    except Exception as e:
        print(f"[AUTH] Password verification error: {e}")
        return False


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("pages.index"))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if verify_password(username, password):
            user = User(username)
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page if next_page else url_for("pages.index"))
        else:
            flash("Invalid username or password", "error")
    
    return render_template("login.html")

@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
