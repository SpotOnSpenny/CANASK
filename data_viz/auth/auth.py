# Standard library imports
from functools import wraps

# External imports
from flask import Blueprint, request, render_template, flash, current_app
from bcrypt import checkpw, gensalt
from flask_login import login_user

# Internal imports
from data_viz.auth import login_manager
from data_viz.database.models import User

# Define the auth blueprint for authentication related routes
auth_blueprint = Blueprint("auth", __name__)

# require_auth decorator
def require_auth(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if session.get("simplelogin", False):
            session["has_visited"] = True
            return view(**kwargs)
        elif not session.get("simplelogin", False):
            if session.get("has_visited", False):
                flash("Please login to access this page", "warning")
            return render_template("base.jinja", include_partials="login")
    return wrapped_view

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
################################# ROUTES ###########################################
@auth_blueprint.route("/v1/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        print(f"Session will expire in: {current_app.config['PERMANENT_SESSION_LIFETIME']}")
        form_data = request.form
        username = form_data.get("username")
        password = form_data.get("password")
        user = User.query.filter_by(username=username).first()
        if user and checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
            login_user(user)
            if request.headers.get("HX-Request") == "true":
                return render_template("index.jinja")
            else:
                return render_template("base.jinja", include_partials="index", dash_template=None)
        else:
            flash("Invalid username or password", "danger")
            return render_template("base.jinja", include_partials="login")
    else:
        return render_template("base.jinja", include_partials="login")