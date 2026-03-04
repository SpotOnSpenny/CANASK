# Standard library imports
from functools import wraps
from datetime import datetime

# External imports
from flask import Blueprint, request, render_template, flash, current_app, redirect, url_for, session
from bcrypt import checkpw, gensalt
from flask_login import login_user, current_user, logout_user

# Internal imports
from data_viz.auth import login_manager
from data_viz.database import db
from data_viz.database.models import User, Invites, Groups, UserGroups, UserActivity

# Define the auth blueprint for authentication related routes
auth_blueprint = Blueprint("auth", __name__)

# require_auth decorator
def require_auth(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if not current_user.is_authenticated:
            flash("You need to be logged in to access this page.", "warning")
            return redirect(url_for("auth.login"))
        return view(**kwargs)
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
            new_login = UserActivity(
                user_id = user.id,
                activity_type = "authentication attempt",
                activity_target_type = "User",
                activity_target_id = user.id,
                details = "Successful login",
                ip_address = request.remote_addr
            )
            db.session.add(new_login)
            db.session.commit()
            if request.headers.get("HX-Request") == "true":
                return render_template("index.jinja")
            else:
                return render_template("base.jinja", include_partials="index", dash_template=None)
        else:
            login_attempt = UserActivity(
                user_id = user.id if user else None,
                activity_type = "authentication attempt",
                activity_target_type = "User",
                activity_target_id = user.id if user else None,
                details = f"Failed login attempt for {user.email if user else 'unknown user'}, using the email {form_data.get('email')}",
                ip_address = request.remote_addr
            )
            db.session.add(login_attempt)
            db.session.commit()
            flash("Invalid username or password", "danger")
            return render_template("base.jinja", include_partials="login")
    else:
        return render_template("base.jinja", include_partials="login")

@auth_blueprint.route("/v1/logout", methods=["POST"])
@require_auth
def logout():
    logout_activity = UserActivity(
        user_id = current_user.id,
        activity_type = "logout",
        activity_target_type = "User",
        activity_target_id = current_user.id,
        details = "User logged out",
        ip_address = request.remote_addr
    )
    db.session.add(logout_activity)
    db.session.commit()
    logout_user()
    return render_template("v1/login.jinja")

@auth_blueprint.route("/v1/invite-user", methods=["GET", "POST"])
@require_auth
def invite_user():
    if request.method == "POST":
        #create the invite in the database
        invite = Invites(
            email=request.form.get("email"),
            group_id=request.form.get("group_id"),
            role=request.form.get("role")
        )
        db.session.add(invite)
        db.session.commit()

        # generate the jwt token for the invite
        payload = {
            "email": request.form.get("email"),
            "group_id": request.form.get("group_id"),
            "role": request.form.get("role"),
            "invite_id": invite.id
        }

@auth_blueprint.route("/v1/accept-invite/<token>", methods=["GET"])
def accept_invite(token):
    pass

@auth_blueprint.route("/v1/invite-management", methods=["GET", "POST"])
def invite_management():
    pass