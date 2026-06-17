# Standard library imports
from functools import wraps
from datetime import datetime, timezone

# External imports
from flask import Blueprint, request, render_template, flash, current_app, redirect, url_for, session, make_response
from bcrypt import checkpw, gensalt
import jwt
from flask_login import login_user, current_user, logout_user
from celery.result import AsyncResult


# Internal imports
from data_viz.auth import login_manager
from data_viz.database import db
from data_viz.database.models import User, Invites, Groups, UserGroups, UserActivity, InviteGroups, DataSources, GroupDataSources, Visuals, GroupVisuals
from data_viz.auth.auth_helpers import get_user_groups, get_assignable_roles, validate_password, create_user, create_group, assign_group, assign_site_admin, get_manageable_users, get_user_memberships_in_groups, get_manageable_groups, set_group_data_sources, owned_data_sources, can_manage_source, teams_for_source, visuals_for_source, set_group_visuals, set_visual_visibility, visibility_rows_for_source
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY
from celery_worker.tasks.invite_jwt_expiry import expire_invite

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
            elif group_id_source == "invite":
                invite_id = kwargs.get("invite_id")
                invite = Invites.query.get(invite_id)
                if not invite:
                    flash("Invite not found", "danger")
                    return redirect(url_for("main.index"))
                group_ids = [ig.group_id for ig in invite.invite_groups]
            elif group_id_source == "user":
                target_user_id = kwargs.get("user_id")
                target_user = User.query.get(target_user_id)
                if not target_user:
                    flash("User not found", "danger")
                    return redirect(url_for("main.index"))
                group_ids = [m.group_id for m in UserGroups.query.filter_by(user_id = target_user_id).all()]
            

            if not group_ids:
                flash("Group ID not specified. Cannot verify permissions.", "danger")
                return redirect(url_for("main.index"))

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
                return redirect(url_for("main.index"))
            elif not groups_with_required_role and action:
                flash(f"You do not have the required permissions to {action}.", "danger")
                return redirect(url_for("main.index"))
            else:
                kwargs["groups_with_required_role"] = groups_with_required_role
            return view(*args, **kwargs)
        return wrapped_view
    return decorator

            

def parse_group_assignments(form_data):
    """Parse the shared invite/add-user form. Returns (site_admin, {group_id: role})."""
    site_admin = False
    group_assignments = {}
    for key, value in form_data.items():
        if "group_assignment" not in key:
            continue
        if value.split("__")[1] == "Site Admin":
            site_admin = True
            return site_admin, {}
        group_name = value.split("__")[0]
        role = value.split("__")[1]
        group = Groups.query.filter_by(name = group_name).first()
        if group:
            group_assignments[group.id] = role
    return site_admin, group_assignments

def create_invite(email, group_assignments, site_admin_invite):
    """Create a brand-new pending invite (+ JWT, expiry task, activity log) and return it."""
    token_expiry = datetime.now(timezone.utc) + current_app.config["INVITE_TOKEN_EXPIRY"]
    invite = Invites(
        email = email,
        status = "pending",
        expires_at = token_expiry,
        sent_by = current_user.id,
        site_admin_invite = site_admin_invite
    )
    db.session.add(invite)
    db.session.flush()

    if not site_admin_invite:
        for group_id, role in group_assignments.items():
            db.session.add(InviteGroups(invite_id = invite.id, group_id = group_id, role = role))

    if site_admin_invite:
        details = f"Site admin invite sent to {invite.email} by {current_user.username}"
    else:
        details = f"Invite sent to {invite.email} by {current_user.username}, for the groups {', '.join([Groups.query.get(gid).name for gid in group_assignments.keys()])} with respective roles {', '.join(group_assignments.values())}"
    db.session.add(UserActivity(
        user_id = current_user.id,
        activity_type = "user_invite",
        activity_target_type = "invite",
        activity_target_id = invite.id,
        details = details,
        ip_address = request.remote_addr
    ))

    invite.token = invite.generate_jwt(current_app.config["SECRET_KEY"])
    task = expire_invite.apply_async(args = [invite.id], eta = token_expiry)
    invite.expiry_task_id = task.id
    db.session.commit()
    return invite

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
            response = make_response(render_template("index.jinja"))
            response.headers["HX-Push-Url"] = "/"
            return response

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
            if request.headers.get("HX-Request"):
                return render_template("v1/login.jinja")
            else:
                return render_template("base.jinja", include_partials="login")
    else:
        if request.headers.get("HX-Request"):
            return render_template("v1/login.jinja")
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
    # Public pages now exist, so drop the (now anonymous) user on a fresh home page rather than the
    # login screen. HX-Redirect makes HTMX do a full client-side navigation, which re-renders the
    # menu/nav for the logged-out state; fall back to a normal redirect for non-HTMX requests.
    if request.headers.get("HX-Request") == "true":
        return ("", 204, {"HX-Redirect": "/"})
    return redirect(url_for("main.index"))

@auth_blueprint.route("/v1/invite-user", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source = "all_groups", action = "invite new users")
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
        email = request.form.get("email")

        # Parse form data first so we know what's being requested
        site_admin_invite, group_assignments = parse_group_assignments(request.form.to_dict())

        # Check to ensure no user with that email already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash(f"A user with the email {email} already exists. Assign a new role/group to the existing user instead.", "danger")
            return redirect(request.referrer or url_for("auth.invite_user"))

        # Check if a pending invite already exists for this email
        existing_invite = Invites.query.filter_by(email=email, status="pending").first()

        if existing_invite:
            # Case 1: existing invite is site admin — block everything
            if existing_invite.site_admin_invite:
                flash(f"A pending invite for {email} already exists with elevated site admin permissions.", "warning")
                return redirect(request.referrer or url_for("auth.invite_user"))

            # Case 2: new invite is site admin — upgrade existing invite
            if site_admin_invite:
                existing_invite.site_admin_invite = True
                activity = UserActivity(
                    user_id=current_user.id,
                    activity_type="user_invite",
                    activity_target_type="invite",
                    activity_target_id=existing_invite.id,
                    details=f"Invite for {email} upgraded to site admin by {current_user.username}",
                    ip_address=request.remote_addr
                )
                db.session.add(activity)
                db.session.commit()
                flash(f"An invite already exists for {email} and has been upgraded to site admin level.", "success")
                return redirect(url_for("main.index"))

            # Case 3: regular group invite — check for duplicate groups
            existing_group_ids = [ig.group_id for ig in existing_invite.invite_groups]
            duplicate_groups = [gid for gid in group_assignments.keys() if gid in existing_group_ids]
            
            if duplicate_groups:
                duplicate_names = ", ".join([Groups.query.get(gid).name for gid in duplicate_groups])
                flash(f"A pending invite for {email} to {duplicate_names} already exists.", "warning")
                return redirect(request.referrer or url_for("auth.invite_user"))

            # Case 4: add new groups to existing invite
            for group_id, role in group_assignments.items():
                invite_group = InviteGroups(
                    invite_id=existing_invite.id,
                    group_id=group_id,
                    role=role
                )
                db.session.add(invite_group)
            
                activity = UserActivity(
                    user_id=current_user.id,
                    activity_type="user_invite",
                    activity_target_type="invite",
                    activity_target_id=existing_invite.id,
                    details=f"Groups {', '.join([Groups.query.get(gid).name for gid in group_assignments.keys()])} added to existing invite for {email} by {current_user.username}",
                    ip_address=request.remote_addr
                )
                db.session.add(activity)
                db.session.commit()
                group_names = ", ".join([Groups.query.get(gid).name for gid in group_assignments.keys()])
                flash(f"An invite already exists for {email}, {group_names} has been added to the existing invite.", "success")
                return redirect(url_for("main.index"))

        # Create the invite and invite groups in the database tables
        invite = create_invite(email, group_assignments, site_admin_invite)

        # Send the email to the user with the token and instructions to accept the invite
        # Send email here
        flash(f"Invite sent to {invite.email}. JWT = {invite.token}", "success")
        return redirect(url_for("main.index"))

@auth_blueprint.route("/v1/invite-management", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source = "all_groups", action = "manage_invites")
def invite_management(groups_with_required_role = None):
    if current_user.site_admin:
        invites = Invites.query.all()
        managed_group_ids = None  # None means show all
    else:
        managed_group_ids = list(groups_with_required_role.keys())
        invite_ids = db.session.query(InviteGroups.invite_id).filter(
            InviteGroups.group_id.in_(managed_group_ids)
        ).subquery()
        invites = Invites.query.filter(
            Invites.id.in_(invite_ids),
            Invites.status != "revoked"
        ).all()

    if request.headers.get("HX-Request") == "true":
        return render_template("v1/invite_management.jinja", 
                            invites=invites, 
                            managed_group_ids=managed_group_ids,
                            groups_with_required_role=groups_with_required_role,
                            role_hierarchy=ROLE_HIERARCHY)
    else:
        return render_template("base.jinja", 
                            include_partials="index", 
                            dash_template="v1/invite_management.jinja", 
                            invites=invites, 
                            managed_group_ids=managed_group_ids,
                            groups_with_required_role=groups_with_required_role,
                            role_hierarchy=ROLE_HIERARCHY)
    
@auth_blueprint.route("/v1/invites/<int:invite_id>/revoke", methods=["POST"])
@require_auth
@require_role("Group Admin", group_id_source="invite", action="revoke that invite")
def revoke_invite(invite_id, groups_with_required_role=None):
    invite = Invites.query.get(invite_id)

    # Completely revoke the invite if the site admin says to
    if current_user.site_admin:
        if invite.expiry_task_id:
            AsyncResult(invite.expiry_task_id).revoke()
        invite.status = "revoked"
    # Otherwise, just revoke the roles that the user has access to
    else:
        managed_group_ids = list(groups_with_required_role.keys())
        for ig in invite.invite_groups:
            if ig.group_id in managed_group_ids:
                db.session.delete(ig)

        remaining_groups = [ig for ig in invite.invite_groups if ig.group_id not in managed_group_ids]
        if not remaining_groups:
            if invite.expiry_task_id:
                AsyncResult(invite.expiry_task_id).revoke()
            invite.status = "revoked"

    db.session.commit()

    # If the user has no more groups matching the invite, remove the row from the table
    if not current_user.site_admin:
        remaining_managed = [ig for ig in invite.invite_groups if ig.group_id in managed_group_ids]
        if not remaining_managed:
            return "", 200, {"HX-Reswap": "delete"}

    # Otherwise, re-render the row without the group that was revoked
    return render_template("v1/partials/invite_row.jinja",
                        invite=invite,
                        managed_group_ids=None if current_user.site_admin else managed_group_ids,
                        groups_with_required_role=groups_with_required_role,
                        role_hierarchy=ROLE_HIERARCHY)


@auth_blueprint.route("/v1/invites/<int:invite_id>/resend", methods=["POST"])
@require_auth
@require_role("Group Admin", group_id_source = "invite", action = "resend that invite")
def resend_invite(invite_id, groups_with_required_role=None):
    invite = Invites.query.get(invite_id)
    managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())
    # Send email here
    return render_template("v1/partials/invite_row.jinja",
                        invite=invite,
                        managed_group_ids=None if current_user.site_admin else managed_group_ids,
                        groups_with_required_role=groups_with_required_role,
                        role_hierarchy=ROLE_HIERARCHY)


@auth_blueprint.route("/v1/invites/<int:invite_id>/renew", methods=["POST"])
@require_auth
@require_role("Group Admin", group_id_source = "invite", action = "renew that invite")
def renew_invite(invite_id, groups_with_required_role=None):
    invite = Invites.query.get(invite_id)
    managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())
    if invite.expiry_task_id:
        AsyncResult(invite.expiry_task_id).revoke()

    # Regenerate new info for the JWT
    new_expiry = datetime.now(timezone.utc) + current_app.config["INVITE_TOKEN_EXPIRY"]
    invite.expires_at = new_expiry
    invite.status = "pending"
    invite.token = invite.generate_jwt(current_app.config["SECRET_KEY"])

    # send email here
    print(f"Invite for {invite.email} renewed. New JWT = {invite.token}")

    # Create new task for celery to handle expiry
    task = expire_invite.apply_async(args=[invite.id], eta=new_expiry)
    invite.expiry_task_id = task.id

    db.session.commit()

    return render_template("v1/partials/invite_row.jinja",
                        invite=invite,
                        managed_group_ids=None if current_user.site_admin else managed_group_ids,
                        groups_with_required_role=groups_with_required_role,
                        role_hierarchy=ROLE_HIERARCHY)


@auth_blueprint.route("/v1/invites/<int:invite_id>/adjust-permissions", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source="invite", action="adjust invite permissions")
def adjust_invite_permissions(invite_id, groups_with_required_role=None):
    invite = Invites.query.get(invite_id)
    managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())

    if request.method == "GET":
        adjustable_groups = []
        existing_group_ids = [ig.group_id for ig in invite.invite_groups]
        
        # If we are a site admin, show all groups
        if current_user.site_admin:
            all_groups = Groups.query.all()
            for group in all_groups:
                existing_ig = next((ig for ig in invite.invite_groups if ig.group_id == group.id), None)
                if existing_ig:
                    assignable_roles = get_assignable_roles(current_user, group.id)
                    adjustable_groups.append({
                        "invite_group": existing_ig,
                        "assignable_roles": assignable_roles
                    })
                else:
                    # If the group invite does not exist, we need one so that the site admin can add groups via the template
                    temp_ig = InviteGroups(invite_id=invite.id, group_id=group.id, role=None)
                    temp_ig.group = group
                    assignable_roles = get_assignable_roles(current_user, group.id)
                    adjustable_groups.append({
                        "invite_group": temp_ig,
                        "assignable_roles": assignable_roles
                    })
        # If the user isn't a site admin, just render what they have access to
        else:
            for ig in invite.invite_groups:
                if ig.group_id in managed_group_ids:
                    assignable_roles = get_assignable_roles(current_user, ig.group_id)
                    adjustable_groups.append({
                        "invite_group": ig,
                        "assignable_roles": assignable_roles
                    })

        return render_template("v1/partials/adjust_permissions_modal.jinja",
                            invite=invite,
                            adjustable_groups=adjustable_groups)

    # Adjust the invite permissions in the DB based on the form submission
    if request.method == "POST":
        form_data = request.form.to_dict()
        managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())

        # Elevate existing invite to site admin
        if form_data.get("site_admin_invite") == "true":
            invite.site_admin_invite = True
            activity = UserActivity(
                user_id=current_user.id,
                activity_type="invite_permissions_adjusted",
                activity_target_type="invite",
                activity_target_id=invite.id,
                details=f"Invite for {invite.email} elevated to site admin by {current_user.username}",
                ip_address=request.remote_addr
            )
            db.session.add(activity)
            db.session.commit()
            return render_template("v1/partials/invite_row.jinja",
                                invite=invite,
                                managed_group_ids=managed_group_ids,
                                groups_with_required_role=groups_with_required_role,
                                role_hierarchy=ROLE_HIERARCHY)
        
        # If there's no site admin on the form then it's not a site admin invite
        invite.site_admin_invite = False

        # Remove the permissions that were altered
        for ig in invite.invite_groups:
            if managed_group_ids is None or ig.group_id in managed_group_ids:
                db.session.delete(ig)
        db.session.flush()
        new_igs = []
        # Add on the new/altered permisions
        for key, value in form_data.items():
            if key.startswith("role_"):
                group_id = int(key.split("_")[1])
                if managed_group_ids is None or group_id in managed_group_ids:
                    new_ig = InviteGroups(
                        invite_id=invite.id,
                        group_id=group_id,
                        role=value
                    )
                    new_igs.append(new_ig)
                    db.session.add(new_ig)

        db.session.flush()

        # Log who changed what
        group_details = ", ".join([f"{Groups.query.get(ig.group_id).name} | {ig.role}" for ig in new_igs])
        activity = UserActivity(
            user_id=current_user.id,
            activity_type="invite_permissions_adjusted",
            activity_target_type="invite",
            activity_target_id=invite.id,
            details=f"Permissions adjusted on invite for {invite.email} by {current_user.username}. New assignments: {group_details}",
            ip_address=request.remote_addr
        )
        db.session.add(activity)
        db.session.commit()
        return render_template("v1/partials/invite_row.jinja",
                            invite=invite,
                            managed_group_ids=managed_group_ids,
                            groups_with_required_role=groups_with_required_role,
                            role_hierarchy=ROLE_HIERARCHY)

@auth_blueprint.route("/v1/user-management", methods=["GET"])
@require_auth
@require_role("Group Admin", group_id_source = "all_groups", action = "manage users")
def user_management(groups_with_required_role = None):
    managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())
    users = get_manageable_users(current_user)

    user_rows = []
    for user in users:
        memberships = get_user_memberships_in_groups(user.id, managed_group_ids)
        # Non-admins only manage people who share a group they're a Group Admin (or higher) of
        if not current_user.site_admin and not memberships:
            continue
        user_rows.append({"user": user, "memberships": memberships})

    if request.headers.get("HX-Request") == "true":
        return render_template("v1/user_management.jinja",
                            user_rows = user_rows,
                            managed_group_ids = managed_group_ids,
                            groups_with_required_role = groups_with_required_role,
                            role_hierarchy = ROLE_HIERARCHY)
    else:
        return render_template("base.jinja",
                            include_partials = "index",
                            dash_template = "v1/user_management.jinja",
                            user_rows = user_rows,
                            managed_group_ids = managed_group_ids,
                            groups_with_required_role = groups_with_required_role,
                            role_hierarchy = ROLE_HIERARCHY)


@auth_blueprint.route("/v1/add-user", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source = "all_groups", action = "add users")
def add_user(groups_with_required_role = None):
    if request.method == "GET":
        template_data = {}
        if current_user.site_admin:
            template_data["Site Wide"] = ["Site Admin"]
            for group in Groups.query.all():
                template_data[group.name] = ["Group Admin", "Data Owner", "Data Viewer"]
        else:
            for group_id in groups_with_required_role.keys():
                group_obj = Groups.query.get(group_id)
                assignable_roles = get_assignable_roles(current_user, group_id)
                if assignable_roles:
                    template_data[group_obj.name] = assignable_roles

        # Always a modal partial — launched from the User Management page
        return render_template("v1/add_user.jinja", invitable_roles = template_data)

    if request.method == "POST":
        email = request.form.get("email")
        site_admin_assignment, group_assignments = parse_group_assignments(request.form.to_dict())
        existing_user = User.query.filter_by(email = email).first()

        # Existing account → assign directly (no invite needed)
        if existing_user:
            if existing_user.id == current_user.id:
                flash("You cannot change your own access.", "danger")
                return redirect(url_for("auth.user_management"))

            changes = 0
            if site_admin_assignment:
                if not current_user.site_admin:
                    flash("Only site admins can grant site admin access.", "danger")
                elif existing_user.site_admin:
                    flash(f"{existing_user.username} is already a site admin.", "info")
                else:
                    assign_site_admin(existing_user.id, assigned_by = current_user.id)
                    changes += 1
            else:
                for group_id, role in group_assignments.items():
                    group_name = Groups.query.get(group_id).name
                    if not current_user.site_admin and role not in get_assignable_roles(current_user, group_id):
                        flash(f"You cannot assign the role {role} in {group_name}.", "danger")
                        continue
                    existing = UserGroups.query.filter_by(user_id = existing_user.id, group_id = group_id).first()
                    if existing and existing.role == role:
                        flash(f"{existing_user.username} already has the role {role} in {group_name}.", "info")
                        continue
                    # Guardrail: never act on a member who outranks/equals you in that group
                    if existing and not current_user.site_admin:
                        manager_role = groups_with_required_role.get(group_id)
                        if not manager_role or ROLE_HIERARCHY[existing.role] >= ROLE_HIERARCHY[manager_role]:
                            flash(f"You cannot change {existing_user.username}'s role in {group_name}.", "danger")
                            continue
                    assign_group(existing_user.id, group_id, role, assigned_by = current_user.id)
                    changes += 1

            if changes:
                flash(f"Access updated for {existing_user.username}.", "success")
            return redirect(url_for("auth.user_management"))

        # No account yet → fall back to the invite flow
        existing_invite = Invites.query.filter_by(email = email, status = "pending").first()
        if existing_invite:
            flash(f"A pending invite for {email} already exists. Use invite management to adjust it.", "warning")
            return redirect(url_for("auth.user_management"))

        invite = create_invite(email, group_assignments, site_admin_assignment and current_user.site_admin)
        flash(f"No account exists for {email}, so an invite was sent. JWT = {invite.token}", "success")
        return redirect(url_for("auth.user_management"))


@auth_blueprint.route("/v1/users/<int:user_id>/adjust-permissions", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source = "user", action = "adjust user permissions")
def adjust_user_permissions(user_id, groups_with_required_role = None):
    target = User.query.get(user_id)
    if not target:
        flash("User not found.", "danger")
        return redirect(url_for("auth.user_management"))

    managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())

    # Self guardrail — a manager can never act on their own membership
    if user_id == current_user.id:
        flash("You cannot change your own access.", "danger")
        return "", 200, {"HX-Reswap": "none"}

    def in_scope(group_id, membership):
        """Groups this manager may touch for this user: site admins anything,
        others only groups they out-rank the member's current role in."""
        if current_user.site_admin:
            return True
        manager_role = groups_with_required_role.get(group_id)
        return bool(membership) and bool(manager_role) and \
            ROLE_HIERARCHY[membership.role] < ROLE_HIERARCHY[manager_role]

    if request.method == "GET":
        adjustable_groups = []
        if current_user.site_admin:
            candidate_group_ids = [g.id for g in Groups.query.all()]
        else:
            candidate_group_ids = managed_group_ids

        for group_id in candidate_group_ids:
            membership = UserGroups.query.filter_by(user_id = user_id, group_id = group_id).first()
            # Site admins may also add the user to groups they aren't in yet
            if not current_user.site_admin and not in_scope(group_id, membership):
                continue
            adjustable_groups.append({
                "group": Groups.query.get(group_id),
                "current_role": membership.role if membership else None,
                "assignable_roles": get_assignable_roles(current_user, group_id)
            })

        return render_template("v1/partials/adjust_user_permissions_modal.jinja",
                            target = target, adjustable_groups = adjustable_groups)

    if request.method == "POST":
        form_data = request.form.to_dict()
        if current_user.site_admin:
            scope_group_ids = [g.id for g in Groups.query.all()]
        else:
            scope_group_ids = [
                gid for gid in managed_group_ids
                if in_scope(gid, UserGroups.query.filter_by(user_id = user_id, group_id = gid).first())
            ]

        changed = []
        for group_id in scope_group_ids:
            submitted_role = form_data.get(f"role_{group_id}")
            membership = UserGroups.query.filter_by(user_id = user_id, group_id = group_id).first()
            group_name = Groups.query.get(group_id).name

            if submitted_role:
                if not current_user.site_admin and submitted_role not in get_assignable_roles(current_user, group_id):
                    continue
                if membership and membership.role == submitted_role:
                    continue
                assign_group(user_id, group_id, submitted_role, assigned_by = current_user.id)
                changed.append(f"{group_name} → {submitted_role}")
            elif membership:
                # Omitted (unchecked) group that the user currently belongs to → revoke
                assign_group(user_id, group_id, None, assigned_by = current_user.id, remove = True)
                changed.append(f"{group_name} removed")

        if changed:
            flash(f"Updated access for {target.username}: {', '.join(changed)}.", "success")

        memberships = get_user_memberships_in_groups(user_id, managed_group_ids)
        return render_template("v1/partials/user_row.jinja",
                            user = target, memberships = memberships,
                            managed_group_ids = managed_group_ids,
                            groups_with_required_role = groups_with_required_role,
                            role_hierarchy = ROLE_HIERARCHY)


@auth_blueprint.route("/v1/group-management", methods=["GET"])
@require_auth
@require_role("Data Owner", group_id_source = "all_groups", action = "manage groups")
def group_management(groups_with_required_role = None):
    groups = get_manageable_groups(current_user)
    all_sources = DataSources.query.order_by(DataSources.name).all()

    group_rows = []
    for group in groups:
        source_ids = {gds.data_source_id for gds in GroupDataSources.query.filter_by(group_id = group.id).all()}
        group_sources = [s for s in all_sources if s.id in source_ids]
        group_rows.append({
            "group": group,
            "data_sources": group_sources,
            "source_count": len(group_sources)
        })

    if request.headers.get("HX-Request") == "true":
        return render_template("v1/group_management.jinja", group_rows = group_rows)
    else:
        return render_template("base.jinja",
                            include_partials = "index",
                            dash_template = "v1/group_management.jinja",
                            group_rows = group_rows)


@auth_blueprint.route("/v1/create-group", methods=["GET", "POST"])
@require_auth
@require_role("Data Owner", group_id_source = "all_groups", action = "create groups")
def create_group_route(groups_with_required_role = None):
    if request.method == "GET":
        # Always a modal partial — launched from the Group Management page
        return render_template("v1/create_group.jinja")

    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None

    if not name:
        flash("A group name is required.", "danger")
        return redirect(url_for("auth.group_management"))
    if Groups.query.filter_by(name = name).first():
        flash(f"A group named \"{name}\" already exists.", "danger")
        return redirect(url_for("auth.group_management"))

    group = create_group(name = name, created_by = current_user.id, description = description)

    # A non-site-admin must own the group they just created, otherwise the url-scoped
    # role check would lock them out of managing its data sources.
    if not current_user.site_admin:
        assign_group(current_user.id, group.id, "Data Owner", assigned_by = current_user.id)

    flash(f"Group \"{name}\" created.", "success")
    return redirect(url_for("auth.group_management"))


@auth_blueprint.route("/v1/groups/<int:group_id>/data-sources", methods=["GET", "POST"])
@require_auth
@require_role("Data Owner", group_id_source = "url", action = "manage group data sources")
def group_data_sources(group_id, groups_with_required_role = None):
    group = Groups.query.get(group_id)
    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("auth.group_management"))

    all_sources = DataSources.query.order_by(DataSources.name).all()
    current_ids = {gds.data_source_id for gds in GroupDataSources.query.filter_by(group_id = group_id).all()}

    if request.method == "GET":
        source_rows = [{"source": s, "checked": s.id in current_ids} for s in all_sources]
        return render_template("v1/partials/group_data_sources_modal.jinja",
                            group = group, source_rows = source_rows)

    if request.method == "POST":
        valid_ids = {s.id for s in all_sources}
        submitted = []
        for raw in request.form.getlist("source_ids"):
            try:
                sid = int(raw)
            except (TypeError, ValueError):
                continue
            if sid in valid_ids:
                submitted.append(sid)

        changes = set_group_data_sources(group_id, submitted, changed_by = current_user.id)
        if changes:
            flash(f"Data source access updated for {group.name}.", "success")

        submitted_set = set(submitted)
        group_sources = [s for s in all_sources if s.id in submitted_set]
        return render_template("v1/partials/group_row.jinja",
                            group = group,
                            data_sources = group_sources,
                            source_count = len(group_sources))


# --------------------------------------------------------------------------------------- #
# Data Ownership: control which groups (teams) can see which visuals, scoped to data a user owns.
# --------------------------------------------------------------------------------------- #

def _team_grant(group_id, source_visual_ids):
    """Visuals (within a source's scope) currently granted to a group, for the page/row summary."""
    granted_ids = ({gv.visual_id for gv in GroupVisuals.query.filter_by(group_id = group_id).all()}
                   & source_visual_ids)
    return Visuals.query.filter(Visuals.id.in_(granted_ids)).all() if granted_ids else []


@auth_blueprint.route("/v1/data-ownership", methods=["GET"])
@require_auth
@require_role("Data Owner", group_id_source = "all_groups", action = "manage data ownership")
def data_ownership(groups_with_required_role = None):
    ownership = []
    for source in owned_data_sources(current_user):
        source_visual_ids = {v.id for v in Visuals.query.filter_by(data_source_id = source.id).all()}
        teams = [{"group": group, "granted": _team_grant(group.id, source_visual_ids)}
                 for group in teams_for_source(source.id)]
        ownership.append({"source": source, "teams": teams,
                          "visibility_rows": visibility_rows_for_source(source.id),
                          "visual_count": len(source_visual_ids)})

    if request.headers.get("HX-Request") == "true":
        return render_template("v1/data_ownership.jinja", ownership = ownership)
    return render_template("base.jinja", include_partials = "index",
                        dash_template = "v1/data_ownership.jinja", ownership = ownership)


@auth_blueprint.route("/v1/groups/<int:group_id>/sources/<int:source_id>/visuals", methods=["GET", "POST"])
@require_auth
def group_source_visuals(group_id, source_id):
    # Permission here is source-ownership (Data Owner of the source / site admin), not a role in the
    # target team -- so it's checked explicitly rather than via the url-scoped require_role decorator.
    if not can_manage_source(current_user, source_id):
        flash("You do not have permission to manage visuals for this data source.", "danger")
        return redirect(url_for("auth.data_ownership"))

    group = Groups.query.get(group_id)
    source = DataSources.query.get(source_id)
    if not group or not source:
        flash("Group or data source not found.", "danger")
        return redirect(url_for("auth.data_ownership"))

    source_visuals = Visuals.query.filter_by(data_source_id = source_id).all()
    source_visual_ids = {v.id for v in source_visuals}
    current_ids = ({gv.visual_id for gv in GroupVisuals.query.filter_by(group_id = group_id).all()}
                   & source_visual_ids)

    if request.method == "GET":
        return render_template("v1/partials/group_visuals_modal.jinja",
                            group = group, source = source,
                            provinces = visuals_for_source(source_id), current_ids = current_ids)

    # POST: reconcile this group's grants for this source's visuals only
    submitted = [int(raw) for raw in request.form.getlist("visual_ids")
                 if raw.isdigit() and int(raw) in source_visual_ids]
    changes = set_group_visuals(group_id, submitted, source_visual_ids, changed_by = current_user.id)
    if changes:
        flash(f"Visual access for {group.name} updated.", "success")
    return render_template("v1/partials/data_ownership_team_row.jinja",
                        source = source, group = group,
                        granted = _team_grant(group_id, source_visual_ids))


@auth_blueprint.route("/v1/visuals/<int:visual_id>/visibility", methods=["POST"])
@require_auth
def set_visibility(visual_id):
    # Permission is source-ownership (Data Owner of the visual's source / site admin), like the
    # per-team grant route above -- checked explicitly rather than via a role decorator.
    visual = Visuals.query.get(visual_id)
    if not visual or visual.data_source_id is None or not can_manage_source(current_user, visual.data_source_id):
        flash("You do not have permission to change this visual's visibility.", "danger")
        return redirect(url_for("auth.data_ownership"))
    source_id = visual.data_source_id
    try:
        set_visual_visibility(visual_id, request.form.get("visibility", ""), changed_by = current_user.id)
    except ValueError as exc:
        # Flash (delivered via the HX out-of-band swap) and re-render the section unchanged so the
        # toggle states reset.
        flash(str(exc), "danger")
    # Re-render the whole section: a change can cascade to descendant rows, not just this one.
    return render_template("v1/partials/visual_visibility_section.jinja",
                        source = DataSources.query.get(source_id),
                        visibility_rows = visibility_rows_for_source(source_id))


@auth_blueprint.route("/v1/accept-invite", methods=["GET", "POST"])
@auth_blueprint.route("/v1/accept-invite/<token>", methods=["GET"])
def accept_invite(token = None):
    # Ensure user is not already logged in
    if current_user.is_authenticated:
        flash("You are already logged in. Please log out to accept the invite and create a different account.", "warning")
        return redirect(url_for("main.index"))
    
    # Handle token from URL
    if token:
        try:
            jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            flash("This invite link has expired.", "danger")
            return redirect(url_for("auth.login"))
        except jwt.InvalidTokenError:
            flash("The invite link is invalid.", "danger")
            return redirect(url_for("auth.login"))
        session["invite_token"] = token
        return redirect(url_for("auth.accept_invite"))

    # Get and decode token from session
    token = session.get("invite_token")
    if not token:
        flash("No invite token provided.", "danger")
        return redirect(url_for("auth.login"))

    try:
        payload = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        session.pop("invite_token", None)
        flash("This invite link has expired.", "danger")
        return redirect(url_for("auth.login"))
    except jwt.InvalidTokenError:
        session.pop("invite_token", None)
        flash("The invite link is invalid.", "danger")
        return redirect(url_for("auth.login"))

    invite = Invites.query.get(payload.get("invite_id"))
    if not invite or invite.status != "pending":
        session.pop("invite_token", None)
        flash("This invite is no longer valid.", "danger")
        return redirect(url_for("auth.login"))
    
    if request.method == "GET":
        return render_template("base.jinja", 
                            include_partials="accept invite",
                            invite=invite, 
                            email=payload.get("email"))
    
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        # Serverside validations to ensure password is strong, and user doesn't exist
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.accept_invite"))
        valid, message = validate_password(password)
        if not valid:
            flash(message, "danger")
            return redirect(url_for("auth.accept_invite"))
        if User.query.filter_by(username=username).first():
            flash("Username already taken. Please choose a different username.", "danger")
            return redirect(url_for("auth.accept_invite"))

        try:
            # Create the user account
            user = create_user(
                email = payload.get("email"),
                username = username,
                password = password,
                invited_by = invite.sent_by,
                site_admin = invite.site_admin_invite
            )
            
            # Assign group memberships
            if not invite.site_admin_invite:
                invite_groups = InviteGroups.query.filter_by(invite_id=invite.id).all()
                for invite_group in invite_groups:
                    assign_group(
                        user_id=user.id,
                        group_id=invite_group.group_id,
                        role=invite_group.role,
                        assigned_by=invite.sent_by
                    )
        except Exception as e:
            db.session.rollback()
            flash("We couldn't create your account just now. Please try again. If it keeps happening, email spencer.fietz@ucalgary.ca.", "danger")
            current_app.logger.error(f"Error creating account from invite: {str(e)}")
            # Keep the invite context so the user can retry rather than dumping to login
            return redirect(url_for("auth.accept_invite"))

        # Add account creation to the activity log
        activity = UserActivity(
            user_id = user.id,
            activity_type = "account created via invite",
            activity_target_type = "account",
            activity_target_id = user.id,
            details = f"Invite accepted by {user.username}",
            ip_address = request.remote_addr
        )
        db.session.add(activity)
        db.session.commit()

        # Remove the expiry task from the queue since the invite has been accepted
        if invite.expiry_task_id:
            AsyncResult(invite.expiry_task_id).revoke()
        
        # Remove the token from the session and send user to login page
        session.pop("invite_token", None)
        flash("Account created successfully! Please log in to use CANASK.", "success")
        return redirect(url_for("auth.login"))