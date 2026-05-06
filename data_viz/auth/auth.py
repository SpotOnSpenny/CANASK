# Standard library imports
from functools import wraps
from datetime import datetime

# External imports
from flask import Blueprint, request, render_template, flash, current_app, redirect, url_for, session
from bcrypt import checkpw, gensalt
import jwt
from flask_login import login_user, current_user, logout_user

# Internal imports
from data_viz.auth import login_manager
from data_viz.database import db
from data_viz.database.models import User, Invites, Groups, UserGroups, UserActivity, InviteGroups
from data_viz.auth.auth_helpers import get_user_groups, get_assignable_roles
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY

# Define the auth blueprint for authentication related routes
auth_blueprint = Blueprint("auth", __name__)

# Decorator to check if user is authenticated or not
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

# Decorator to check if user has required role for specified group
def require_role(role, group_id_source, action = None):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            # Site admins bypass all role checks, can do and access everything
            if current_user.site_admin:
                kwargs["groups_with_required_role"] = "all"
                return view(*args, **kwargs)
            
            # group ID from source specified
            group_ids = None
            if group_id_source == "form":
                group_ids = [request.form.get("group_id")]
            elif group_id_source == "url":
                group_ids = [kwargs.get("group_id")]
            elif group_id_source == "all_groups":
                user_groups = get_user_groups(current_user)
                group_ids = [group.id for group in user_groups]
            

            if not group_ids:
                flash("Group ID not specified. Cannot verify permissions.", "danger")
                return redirect(request.referrer or url_for("main.index"))

            groups_with_required_role = {}
            for group_id in group_ids:
                # Users membership for specified group
                membership = UserGroups.query.filter_by(
                    user_id = current_user.id,
                    group_id = group_id
                ).first()

                if not membership:
                    continue

                # Check if users role is at least the required role
                user_role_level = ROLE_HIERARCHY.get(membership.role, -1)
                required_role_level = ROLE_HIERARCHY.get(role, 0)
                if user_role_level >= required_role_level:
                    groups_with_required_role[group_id] = membership.role
            if not groups_with_required_role and not action:
                flash("You do not have the required permissions.", "danger")
                return redirect(request.referrer or url_for("main.index"))
            elif not groups_with_required_role and action:
                flash(f"You do not have the required permissions to {action}.", "danger")
                return redirect(request.referrer or url_for("main.index"))
            else:
                kwargs["groups_with_required_role"] = groups_with_required_role
            return view(*args, **kwargs)
        return wrapped_view
    return decorator

            

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
@require_role("group_admin", group_id_source = "all_groups", action = "invite new users")
def invite_user(groups_with_required_role = None):
    if request.method == "GET":
        # Check for site admin role to determine what to show
        template_data = {}
        if current_user.site_admin:
            groups = Groups.query.all()
            template_data["Site Wide"] = ["Site Admin"]
            for group in groups:
                template_data[group.name] = ["Group Admin", "Data Owner", "Data Viewer"]
        else:
            groups = groups_with_required_role.keys()
            for group in groups:
                group_obj = Groups.query.filter_by(id=group).first()
                assignable_roles = get_assignable_roles(current_user, group)
                if assignable_roles:
                    template_data[group_obj.name] = assignable_roles
        if request.headers.get("HX-Request") == "true":
            return render_template("v1/invite_user.jinja", invitable_roles = template_data)
        else:
            return render_template("base.jinja", include_partials="index", dash_template="v1/invite_user.jinja", invitable_roles = template_data)
        return render_template("v1/invite_user.jinja", template_data = template_data)
    

    if request.method == "POST":
        # Check to ensure no user with that email already exists
        existing_user = User.query.filter_by(email=request.form.get("email")).first()
        if existing_user:
            flash(f"A user with the email {request.form.get('email')} already exists. Assign a new role/group to the existing user instead.", "danger")
            return redirect(request.referrer or url_for("auth.invite_user"))
        # Check that an invite with that email is not already pending
        existing_invite = Invites.query.filter_by(email=request.form.get("email"), status = "pending").first()
        if existing_invite:
            flash(f"An invite for the email {request.form.get('email')} is already pending. You must cancel the existing invite before sending a new one.", "danger")
            return redirect(request.referrer or url_for("auth.invite_user"))

        #create the invite and invite groups in the database tables
        form_data = request.form.to_dict()
        site_admin_invite = False
        group_assignments = {}
        for key, value in form_data.items():
            if "group_assignment" in key and value.split("__")[1] == "Site Admin":
                site_admin_invite = True
                break
            elif "group_assignment" in key:
                group_name = value.split("__")[0]
                role = value.split("__")[1]
                group = Groups.query.filter_by(name = group_name).first()
                group_assignments[group.id] = role
            
        token_expiry = datetime.utcnow() + current_app.config["INVITE_TOKEN_EXPIRY"]
        invite = Invites(
            email = request.form.get("email"),
            status = "pending",
            expires_at = token_expiry,
            sent_by = current_user.id,
            site_admin_invite = site_admin_invite
        )
        db.session.add(invite)
        db.session.flush()

        if not site_admin_invite:
            for group_id, role in group_assignments.items():
                invite_group = InviteGroups(
                    invite_id = invite.id,
                    group_id = group_id,
                    role = role
                )
                db.session.add(invite_group)
        
        details = f"Invite sent to {invite.email} by {current_user.username}, for the groups {', '.join([Groups.query.get(group_id).name for group_id in group_assignments.keys()])} with respective roles {', '.join(group_assignments.values())}" if not site_admin_invite else f"Site admin invite sent to {invite.email} by {current_user.username}"
        activity = UserActivity(
            user_id = current_user.id,
            activity_type = "user_invite",
            activity_target_type = "invite",
            activity_target_id = invite.id,
            details = details,
            ip_address = request.remote_addr
        )
        db.session.add(activity)
        db.session.commit()

        # Queries to check that the invite was generated correctly
        print("Invite result:")
        print(Invites.query.filter_by(id = invite.id).first().__dict__)
        print("Invite groups result:")
        for ig in InviteGroups.query.filter_by(invite_id = invite.id).all():
            print(ig.__dict__)
        print("Invite activity result:")
        print(UserActivity.query.filter_by(activity_type = "user_invite").first().__dict__)

        payload = {
            "email": request.form.get("email"),
            "invite_id": invite.id,
            "exp": token_expiry.timestamp()
        }
        token = jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm = "HS256")

        # Send the email to the user with the token and instructions to accept the invite
        flash(f"Invite sent to {invite.email}. JWT = {token}", "success")
        return redirect(url_for("main.index"))

@auth_blueprint.route("/v1/accept-invite/<token>", methods=["GET"])
def accept_invite(token):
    pass

@auth_blueprint.route("/v1/invite-management", methods=["GET", "POST"])
def invite_management():
    pass