# Standard library imports
from functools import wraps
from datetime import datetime, timezone
import json

# External imports
from flask import Blueprint, request, render_template, flash, current_app, redirect, url_for, session, make_response
from bcrypt import checkpw, gensalt, hashpw
import jwt
from flask_login import login_user, current_user, logout_user
from flask_wtf.csrf import generate_csrf
from celery.result import AsyncResult
from sqlalchemy import or_, func


# Internal imports
from data_viz.auth import login_manager
from data_viz.database import db
from data_viz.database.models import User, Invites, Groups, UserGroups, UserActivity, InviteGroups, DataSources, GroupDataSources, Visuals, GroupVisuals
from data_viz.auth.auth_helpers import get_user_groups, get_assignable_roles, validate_password, create_user, create_group, assign_group, assign_site_admin, get_manageable_users, get_user_memberships_in_groups, get_manageable_groups, set_group_data_sources, owned_data_sources, can_manage_source, teams_for_source, visuals_for_source, set_group_visuals, set_visual_visibility, set_source_visibility, set_province_default_visual, visibility_rows_for_source, is_last_active_site_admin, removal_password_is_set, check_removal_password, deactivate_user
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY
from data_viz.extensions import limiter
from data_viz.email import send_ses_email
from data_viz.recaptcha import verify_recaptcha
from data_viz.validation import validate_email, validate_username, validate_text, validate_role, MAX_GROUP_NAME, MAX_GROUP_DESC
from celery_worker.tasks.invite_jwt_expiry import expire_invite

# Define the auth blueprint for authentication related routes
auth_blueprint = Blueprint("auth", __name__)

# A fixed bcrypt hash compared against when no user matches, so a missing identifier costs the same
# ~bcrypt time as a wrong password -- closes the login timing side-channel that leaks valid usernames.
_DUMMY_PASSWORD_HASH = hashpw(b"password-does-not-matter", gensalt())


def recent_login_failures(user_id, ip_address=None):
    """Count this account's failed login attempts inside the lockout window (see LOGIN_LOCKOUT_*),
    optionally restricted to one source IP. Failures before the account's most recent successful
    login don't count -- a legitimate user who mistyped near the threshold must not be locked out
    right after authenticating."""
    cutoff = datetime.now(timezone.utc) - current_app.config["LOGIN_LOCKOUT_WINDOW"]
    query = UserActivity.query.filter(
        UserActivity.user_id == user_id,
        UserActivity.activity_type == "authentication attempt",
        UserActivity.details.like("Failed login%"),
        UserActivity.timestamp >= cutoff,
    )
    last_success = (UserActivity.query.filter(
        UserActivity.user_id == user_id,
        UserActivity.activity_type == "authentication attempt",
        UserActivity.details == "Successful login",
        UserActivity.timestamp >= cutoff,
    ).order_by(UserActivity.timestamp.desc()).first())
    if last_success:
        query = query.filter(UserActivity.timestamp > last_success.timestamp)
    if ip_address:
        query = query.filter(UserActivity.ip_address == ip_address)
    return query.count()


def login_locked_out(user):
    """Two-dimension lockout so it can't be weaponized against a known account:
    - per (account, source IP): trips at LOGIN_LOCKOUT_THRESHOLD -- an attacker hammering from
      their own IP(s) locks only those IPs out of the account, not the legitimate user.
    - account-wide: a higher ceiling (LOGIN_LOCKOUT_ACCOUNT_THRESHOLD) that still bounds a
      distributed, IP-rotating brute force."""
    if recent_login_failures(user.id, request.remote_addr) >= current_app.config["LOGIN_LOCKOUT_THRESHOLD"]:
        return True
    return recent_login_failures(user.id) >= current_app.config["LOGIN_LOCKOUT_ACCOUNT_THRESHOLD"]


def _log_auth_attempt(user, identifier, details):
    """Append a UserActivity row for a login attempt (success/failure/blocked)."""
    db.session.add(UserActivity(
        user_id=user.id if user else None,
        activity_type="authentication attempt",
        activity_target_type="User",
        activity_target_id=user.id if user else None,
        details=details,
        ip_address=request.remote_addr,
    ))
    db.session.commit()


def recent_removal_password_failures(user_id=None):
    """Count failed removal-password attempts inside the lockout window (see REMOVAL_LOCKOUT_*).
    With user_id, the acting admin's own failures; without, all actors' -- the secret is shared,
    so the global count bounds a brute force spread across several compromised admin accounts."""
    cutoff = datetime.now(timezone.utc) - current_app.config["REMOVAL_LOCKOUT_WINDOW"]
    query = UserActivity.query.filter(
        UserActivity.activity_type == "removal_password_attempt",
        UserActivity.details.like("Failed removal%"),
        UserActivity.timestamp >= cutoff,
    )
    if user_id is not None:
        query = query.filter(UserActivity.user_id == user_id)
    return query.count()


def removal_locked_out(user):
    if recent_removal_password_failures(user.id) >= current_app.config["REMOVAL_LOCKOUT_THRESHOLD"]:
        return True
    return recent_removal_password_failures() >= current_app.config["REMOVAL_LOCKOUT_GLOBAL_THRESHOLD"]


def _log_removal_attempt(details, target=None):
    """Append a UserActivity row for a removal-password attempt. Failure details must start with
    'Failed removal' -- that's the exact prefix recent_removal_password_failures counts -- and
    'Blocked ...' details must NOT, or a lockout would extend itself."""
    db.session.add(UserActivity(
        user_id=current_user.id,
        activity_type="removal_password_attempt",
        activity_target_type="user" if target else "removal_password",
        activity_target_id=target.id if target else None,
        details=details,
        ip_address=request.remote_addr,
    ))
    db.session.commit()


def _check_admin_grant_credentials(action_desc, target=None):
    """Verify the actor's own password + the shared removal password for a protected site-admin
    membership change (removal, elevation, site-admin invite). Returns None when both pass;
    otherwise the error message, having logged the failure with the 'Failed removal' prefix the
    lockout counter matches -- every path spends the same shared secret, so they all share the
    lockout. Naming the wrong field in the message is fine: the actor already knows their own
    password, so it is no oracle for the shared secret."""
    own_password = request.form.get("own_password") or ""
    if not checkpw(own_password.encode("utf-8"), current_user.password_hash.encode("utf-8")):
        _log_removal_attempt(f"Failed removal confirmation for {action_desc}: "
                             "incorrect account password", target=target)
        return "Your account password was incorrect."
    if not check_removal_password(request.form.get("removal_password")):
        _log_removal_attempt(f"Failed removal confirmation for {action_desc}: "
                             "incorrect removal password", target=target)
        return "The removal password was incorrect."
    return None


def _site_admin_grant_gate(action_desc, target=None):
    """The full gate for granting site admin access from a plain form flow (the invite page and
    the add-user modal): unset-secret guard, lockout, then both password checks. Returns None on
    success or the message to flash. The modal flows (remove/make admin) run the same pieces
    individually because each failure kind renders differently there."""
    if not removal_password_is_set():
        return ("No removal password has been set. Run `flask rotate-removal-password` "
                "on the server to set one first.")
    if removal_locked_out(current_user):
        _log_removal_attempt(f"Blocked {action_desc}: locked out", target=target)
        return "Too many failed attempts. Try again later."
    return _check_admin_grant_credentials(action_desc, target=target)


def _render_login():
    """Render the login page as a bare partial for HTMX or the full base page otherwise."""
    if request.headers.get("HX-Request"):
        return render_template("v1/login.jinja")
    return render_template("base.jinja", include_partials="login")


def send_invite_email(invite):
    """Email the invitee their accept link. The token is a bearer credential, so it is ONLY ever sent
    over this email channel -- never flashed or logged. Returns True on success, False on failure
    (callers keep the invite regardless and surface a soft warning)."""
    base = current_app.config.get("PUBLIC_BASE_URL")
    if not base:
        current_app.logger.error("PUBLIC_BASE_URL is not set; cannot build invite link for %s", invite.email)
        return False
    accept_url = f"{base}/v1/accept-invite/{invite.token}"
    subject = "You've been invited to CANASK"
    html_body = f"""
        <p>You've been invited to create an account on CANASK.</p>
        <p><a href="{accept_url}">Accept your invite</a> to set up your account.</p>
        <p>Or paste this link into your browser:<br>{accept_url}</p>
        <p>This link expires shortly for security. If it has expired, ask your administrator to resend the invite.</p>
        """
    return send_ses_email([invite.email], subject, html_body)

# Decorator to check if user is authenticated or not
def require_auth(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        # The explicit is_active check is belt-and-braces with load_user below: deactivation must be
        # an immediate kill-switch, not depend on the UserMixin.is_authenticated -> is_active subtlety.
        if not current_user.is_authenticated or not current_user.is_active:
            flash("You need to be logged in to access this page.", "warning")
            return redirect(url_for("auth.login"))
        return view(**kwargs)
    return wrapped_view

@login_manager.user_loader
def load_user(user_id):
    user = User.query.get(int(user_id))
    # A deactivated account's existing session dies here: returning None makes Flask-Login treat the
    # request as anonymous on its very next request, regardless of the session cookie's lifetime.
    if user and not user.is_active:
        return None
    return user

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
    """Parse the shared invite/add-user form. Returns (site_admin, {group_id: role}, skipped).

    Each group_assignment value is expected to be exactly "<group name>__<role>". Values not matching
    that shape (hostile/garbled form input), unknown roles, and group names that no longer resolve
    (e.g. a group renamed between page render and submit) are skipped rather than raising -- but they
    are returned in `skipped` so callers can tell the admin what was dropped instead of silently
    creating a lesser invite. Authorization (whether the caller may grant that role) is enforced
    separately by validate_group_assignments."""
    site_admin = False
    group_assignments = {}
    skipped = []
    for key, value in form_data.items():
        if "group_assignment" not in key:
            continue
        parts = (value or "").split("__")
        if len(parts) != 2:
            skipped.append(value or "(empty)")
            continue
        group_name, role = parts
        if role == "Site Admin":
            site_admin = True
            return site_admin, {}, []
        role_ok, _ = validate_role(role)
        if not role_ok:
            skipped.append(f"{group_name} ({role})")
            continue
        group = Groups.query.filter_by(name = group_name).first()
        if group:
            group_assignments[group.id] = role
        else:
            skipped.append(f"{group_name} ({role})")
    return site_admin, group_assignments, skipped


def flash_skipped_assignments(skipped):
    """Surface any assignments parse_group_assignments dropped, so a partially-applied form never
    renders a success-looking response without explanation."""
    if skipped:
        flash(f"These assignments were not applied (unknown group or role): {', '.join(skipped)}", "warning")

def validate_group_assignments(group_assignments, groups_with_required_role):
    """Authorize a parsed {group_id: role} map against the caller's permissions.

    Returns (ok, error_message). Site admins may assign anything. Otherwise the caller must manage
    the group (it must appear in the decorator-injected groups_with_required_role) and the role must
    be one they're allowed to grant there (get_assignable_roles enforces the no-outrank rule). The
    invite/add-user handlers must call this before persisting assignments -- the role decorators only
    prove the caller manages *some* group, not that the submitted group/role pairs are in scope."""
    if current_user.site_admin:
        return True, None
    for group_id, role in group_assignments.items():
        if group_id not in groups_with_required_role:
            return False, "You can only assign roles in groups you manage."
        if role not in get_assignable_roles(current_user, group_id):
            group_name = Groups.query.get(group_id).name
            return False, f"You cannot assign the role {role} in {group_name}."
    return True, None

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

    invite.token = invite.generate_jwt(current_app.config["INVITE_JWT_SECRET"])
    task = expire_invite.apply_async(args = [invite.id], eta = token_expiry)
    invite.expiry_task_id = task.id
    db.session.commit()
    return invite

################################# ROUTES ###########################################
@auth_blueprint.route("/v1/login", methods=["GET", "POST"])
# Throttle only POST (credential attempts) to blunt brute-forcing; the GET login page is unlimited.
@limiter.limit(lambda: current_app.config["RATELIMIT_LOGIN"], exempt_when=lambda: request.method == "GET")
def login():
    if request.method == "POST":
        form_data = request.form
        identifier = form_data.get("username")
        password = form_data.get("password")
        # Guard missing fields up front: a None password would crash on .encode() below. Use the same
        # generic message as a bad credential so this doesn't leak which field was missing.
        if not identifier or not password:
            flash("Invalid username or password", "danger")
            return _render_login()

        # Bot gate before any DB work or bcrypt: verify the reCAPTCHA v3 token. The message is
        # generic and identical whether the token was missing, stale, replayed, or low-score, so it
        # leaks nothing. Complements (does not replace) the per-IP rate limit and account lockout.
        recaptcha_ok, _ = verify_recaptcha(form_data.get("recaptcha-token"), "login")
        if not recaptcha_ok:
            flash("Could not verify you're human. Please try again.", "danger")
            return _render_login()

        user = User.query.filter(
            or_(
                User.username == identifier,
                func.lower(User.email) == (identifier or "").lower(),
            )
        ).first()

        # Per-account lockout: refuse before verifying the password once this account has too many recent
        # failures (per-IP first, account-wide ceiling second -- see login_locked_out), so a distributed
        # (IP-rotating) attack against one account is bounded, not just per-IP.
        if user and login_locked_out(user):
            _log_auth_attempt(user, identifier, "Login blocked: too many recent failed attempts")
            flash("Too many failed login attempts. Please wait a few minutes and try again.", "danger")
            return _render_login()

        if user and checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
            # Correct password, but a deactivated account must not get a session. The password was already
            # verified, so this message reveals nothing an attacker couldn't already confirm.
            if not user.is_active:
                _log_auth_attempt(user, identifier, "Login refused: account not active")
                flash("This account is not active. Please contact an administrator.", "danger")
                return _render_login()
            # Session fixation defense: drop any pre-auth session so a fixed pre-login session id can't be
            # reused to ride the authenticated session.
            session.clear()
            login_user(user)
            _log_auth_attempt(user, identifier, "Successful login")
            response = make_response(render_template("index.jinja"))
            response.headers["HX-Push-Url"] = "/"
            # session.clear() above also dropped the session's CSRF token, and this partial swap does
            # not re-render base.jinja's <meta name="csrf-token"> -- without a hand-off, every later
            # POST 400s with a stale token ("CSRF session token is missing"). generate_csrf() seeds a
            # fresh token into the new session (rotating it at privilege change), and the HX-Trigger
            # event delivers it to main.js's csrfTokenRefresh listener, which updates the meta tag.
            response.headers["HX-Trigger"] = json.dumps(
                {"csrfTokenRefresh": {"token": generate_csrf()}})
            return response

        else:
            # Equalize response time for a non-existent identifier (no password check ran above) so login
            # timing can't be used to enumerate valid usernames/emails.
            if not user:
                checkpw(password.encode("utf-8"), _DUMMY_PASSWORD_HASH)
            _log_auth_attempt(
                user, identifier,
                f"Failed login attempt for {user.email if user else 'unknown user'}, using identifier {identifier}")
            flash("Invalid username or password", "danger")
            return _render_login()
    else:
        return _render_login()

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
        email_ok, email = validate_email(request.form.get("email"), required=True)
        if not email_ok:
            flash(email, "danger")
            return redirect(url_for("auth.invite_user"))

        # Parse form data first so we know what's being requested
        site_admin_invite, group_assignments, skipped = parse_group_assignments(request.form.to_dict())
        flash_skipped_assignments(skipped)
        # Refuse a permissionless invite: if every assignment was dropped (or none was submitted),
        # sending it anyway would invite someone into nothing while telling the admin it succeeded.
        if not site_admin_invite and not group_assignments:
            flash("No valid group assignments were submitted, so no invite was created.", "danger")
            return redirect(url_for("auth.invite_user"))

        # Authorize the request before acting on it: the role decorator only proves the caller is a
        # Group Admin in *some* group, not that this site-admin flag / these group+role pairs are in
        # their scope. Without this a Group Admin could mint a Site Admin or assign roles anywhere.
        if site_admin_invite and not current_user.site_admin:
            flash("Only site admins can grant site admin access.", "danger")
            return redirect(url_for("auth.invite_user"))
        # Gate BEFORE any branch: this covers both a fresh site-admin invite and the
        # upgrade-existing-invite path below -- both mint an admin, so both spend the shared
        # removal password (own password + removal password, same lockout as removal).
        if site_admin_invite:
            gate_error = _site_admin_grant_gate(f"site admin invite for {email}")
            if gate_error:
                flash(gate_error, "danger")
                return redirect(url_for("auth.invite_user"))
        ok, error = validate_group_assignments(group_assignments, groups_with_required_role)
        if not ok:
            flash(error, "danger")
            return redirect(url_for("auth.invite_user"))

        # Check to ensure no user with that email already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash(f"A user with the email {email} already exists. Assign a new role/group to the existing user instead.", "danger")
            return redirect(url_for("auth.invite_user"))

        # Check if a pending invite already exists for this email
        existing_invite = Invites.query.filter_by(email=email, status="pending").first()

        if existing_invite:
            # Case 1: existing invite is site admin — block everything
            if existing_invite.site_admin_invite:
                flash(f"A pending invite for {email} already exists with elevated site admin permissions.", "warning")
                return redirect(url_for("auth.invite_user"))

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
                return redirect(url_for("auth.invite_user"))

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

        if send_invite_email(invite):
            flash(f"Invite sent to {invite.email}.", "success")
        else:
            flash(f"Invite created for {invite.email}, but the email could not be sent. "
                  f"Check email configuration or resend the invite.", "warning")
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

    # Only a still-outstanding invite may be renewed. Refuse revoked/accepted ones so a renewal can't
    # resurrect a deliberately-cancelled invite or re-open one whose account already exists (previously
    # any status was flipped back to "pending").
    if invite is None or invite.status not in ("pending", "expired"):
        flash("This invite can no longer be renewed.", "warning")
        if invite is None:
            return ("", 204)
        return render_template("v1/partials/invite_row.jinja",
                            invite=invite,
                            managed_group_ids=managed_group_ids,
                            groups_with_required_role=groups_with_required_role,
                            role_hierarchy=ROLE_HIERARCHY)

    if invite.expiry_task_id:
        AsyncResult(invite.expiry_task_id).revoke()

    # Regenerate new info for the JWT
    new_expiry = datetime.now(timezone.utc) + current_app.config["INVITE_TOKEN_EXPIRY"]
    invite.expires_at = new_expiry
    invite.status = "pending"
    invite.token = invite.generate_jwt(current_app.config["INVITE_JWT_SECRET"])

    # Create new task for celery to handle expiry
    task = expire_invite.apply_async(args=[invite.id], eta=new_expiry)
    invite.expiry_task_id = task.id

    db.session.commit()

    if send_invite_email(invite):
        flash(f"Invite for {invite.email} renewed and re-sent.", "success")
    else:
        flash(f"Invite for {invite.email} renewed, but the email could not be sent.", "warning")

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

        # Elevate existing invite to site admin (site admins only -- the "invite" role scope only
        # proves the caller is a Group Admin sharing a group with this invite).
        if form_data.get("site_admin_invite") == "true":
            if not current_user.site_admin:
                flash("Only site admins can grant site admin access.", "danger")
                return render_template("v1/partials/invite_row.jinja",
                                    invite=invite,
                                    managed_group_ids=managed_group_ids,
                                    groups_with_required_role=groups_with_required_role,
                                    role_hierarchy=ROLE_HIERARCHY)
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
        rejected = []
        # Add on the new/altered permisions
        for key, value in form_data.items():
            if key.startswith("role_"):
                suffix = key.split("_", 1)[1]
                if not suffix.isdigit():   # malformed role_ key -> skip rather than crash on int()
                    continue
                group_id = int(suffix)
                group = Groups.query.get(group_id)
                group_label = group.name if group else f"group {group_id}"
                # The submitted role must be a real, group-assignable role -- validated even for site
                # admins so an arbitrary string can never be written as a role.
                if not validate_role(value)[0]:
                    rejected.append(group_label)
                    continue
                # managed_group_ids is None only for site admins (who may assign any role).
                if managed_group_ids is not None and group_id not in managed_group_ids:
                    rejected.append(group_label)
                    continue
                # A non-site-admin may only assign roles below their own in that group; skip any the
                # caller isn't allowed to grant rather than silently elevating.
                if not current_user.site_admin and value not in get_assignable_roles(current_user, group_id):
                    rejected.append(f"{group_label} ({value})")
                    continue
                new_ig = InviteGroups(
                    invite_id=invite.id,
                    group_id=group_id,
                    role=value
                )
                new_igs.append(new_ig)
                db.session.add(new_ig)
        # Rejecting an out-of-scope role is the correct RBAC outcome, but doing it silently is not --
        # tell the caller which requested changes were dropped instead of rendering pure success.
        if rejected:
            flash(f"Some changes were not applied (invalid role or outside your scope): {', '.join(rejected)}", "warning")

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
        email_ok, email = validate_email(request.form.get("email"), required=True)
        if not email_ok:
            flash(email, "danger")
            return redirect(url_for("auth.user_management"))
        site_admin_assignment, group_assignments, skipped = parse_group_assignments(request.form.to_dict())
        flash_skipped_assignments(skipped)
        if not site_admin_assignment and not group_assignments:
            flash("No valid group assignments were submitted.", "danger")
            return redirect(url_for("auth.user_management"))
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
                    # Elevation of an existing account goes through the protected modal flow
                    # (own password + removal password) -- never a bare form submit.
                    flash(f"{existing_user.username} already has an account. Use the "
                          "\"Make Site Admin\" action on their row instead.", "info")
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

        # Validate the requested group/role pairs before inviting -- mirror the existing-user path
        # above, which the invite fallback previously skipped (letting a Group Admin grant Data Owner
        # in a group they don't manage).
        ok, error = validate_group_assignments(group_assignments, groups_with_required_role)
        if not ok:
            flash(error, "danger")
            return redirect(url_for("auth.user_management"))

        # A site-admin invite mints an admin at a fresh email, so it spends the same shared
        # secret as elevating or removing one -- otherwise the modal gate is a detour, not a wall.
        if site_admin_assignment and current_user.site_admin:
            gate_error = _site_admin_grant_gate(f"site admin invite for {email}")
            if gate_error:
                flash(gate_error, "danger")
                return redirect(url_for("auth.user_management"))

        invite = create_invite(email, group_assignments, site_admin_assignment and current_user.site_admin)
        if send_invite_email(invite):
            flash(f"No account exists for {email}, so an invite was sent.", "success")
        else:
            flash(f"No account exists for {email}; an invite was created but the email could not be sent.", "warning")
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
        rejected = []
        for group_id in scope_group_ids:
            submitted_role = form_data.get(f"role_{group_id}")
            membership = UserGroups.query.filter_by(user_id = user_id, group_id = group_id).first()
            group_name = Groups.query.get(group_id).name

            if submitted_role:
                # Must be a real, group-assignable role -- validated even for site admins so an
                # arbitrary string can never be written as a role.
                if not validate_role(submitted_role)[0]:
                    rejected.append(group_name)
                    continue
                if not current_user.site_admin and submitted_role not in get_assignable_roles(current_user, group_id):
                    rejected.append(f"{group_name} ({submitted_role})")
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
        # Dropped requests must be visible, not folded into a success-looking row render.
        if rejected:
            flash(f"Some changes were not applied (invalid role or outside your scope): {', '.join(rejected)}", "warning")

        memberships = get_user_memberships_in_groups(user_id, managed_group_ids)
        return render_template("v1/partials/user_row.jinja",
                            user = target, memberships = memberships,
                            managed_group_ids = managed_group_ids,
                            groups_with_required_role = groups_with_required_role,
                            role_hierarchy = ROLE_HIERARCHY)


def _removal_refused(message, category = "danger"):
    """Standard refusal for the removal-password modal endpoints. On GET the opener button will
    show the modal no matter what, so an empty body would pop a blank shell (or stale content
    from an earlier modal) -- render the message as modal content instead. On POST: flash + swap
    nothing; the message rides back as an OOB flash (see the after_request hook)."""
    if request.method == "GET":
        return render_template("v1/partials/removal_message_modal.jinja",
                               message = message, category = category)
    flash(message, category)
    return "", 200, {"HX-Reswap": "none"}


def _removal_form_error(template, message, **context):
    """Field-level failure inside one of the removal modals: re-render the form with an inline
    error into the still-open modal (retarget to #modal-container), so the admin's radio choice
    survives and the modal doesn't close on a typo. Password fields are never re-filled."""
    return render_template(template, error = message, **context), 200, {
        "HX-Retarget": "#modal-container", "HX-Reswap": "innerHTML"}


@auth_blueprint.route("/v1/users/<int:user_id>/remove-admin", methods = ["GET", "POST"])
@require_auth
def remove_site_admin(user_id):
    # Site-admin-only, enforced inline: require_role can't express this (site admins bypass it).
    if not current_user.site_admin:
        return _removal_refused("Only site admins can remove site admin access.")

    target = User.query.get(user_id)
    if not target:
        return _removal_refused("User not found.")

    # Self guardrail, same as adjust-permissions -- and it keeps one rogue admin from
    # quietly demoting themself out of the audit trail's reach.
    if user_id == current_user.id:
        return _removal_refused("You cannot remove your own site admin access.")

    if not target.site_admin:
        return _removal_refused(f"{target.username} is not a site admin.", "info")

    if not removal_password_is_set():
        return _removal_refused("No removal password has been set. Run `flask rotate-removal-password` "
                                "on the server to set one first.", "warning")

    if request.method == "GET":
        return render_template("v1/partials/remove_admin_modal.jinja", target = target)

    # POST -- lockout is checked before any bcrypt work or logging.
    if removal_locked_out(current_user):
        _log_removal_attempt(f"Blocked removal attempt against {target.username}: locked out", target = target)
        return _removal_refused("Too many failed attempts. Try again later.")

    removal_action = request.form.get("removal_action")
    if removal_action not in ("demote", "deactivate"):
        # No secret was tested, so nothing is logged against the lockout.
        return _removal_form_error("v1/partials/remove_admin_modal.jinja",
                                   "Choose whether to demote or deactivate the user.",
                                   target = target)

    error = _check_admin_grant_credentials(f"removal of {target.username}", target = target)
    if error:
        return _removal_form_error("v1/partials/remove_admin_modal.jinja", error,
                                   target = target, selected_action = removal_action)

    if is_last_active_site_admin(target.id):
        return _removal_refused(f"{target.username} is the only remaining active site admin "
                                "and cannot be removed.")

    if removal_action == "demote":
        assign_site_admin(target.id, remove = True, assigned_by = current_user.id)
        mode_msg = "demoted to a regular user"
    else:
        deactivate_user(target.id, deactivated_by = current_user.id, ip_address = request.remote_addr)
        mode_msg = "deactivated"

    db.session.add(UserActivity(
        user_id = current_user.id,
        activity_type = "site_admin_removal",
        activity_target_type = "user",
        activity_target_id = target.id,
        details = f"{target.username} was {mode_msg} by {current_user.username} ({removal_action}).",
        ip_address = request.remote_addr))
    db.session.commit()

    # Deliberately no email: admin-membership changes are quiet, audit-row-only events.
    flash(f"{target.username} was {mode_msg}.", "success")
    return _render_admin_user_row(target)


def _render_admin_user_row(target):
    """Re-render a user's row after a site-admin membership change, with full (site-admin)
    context -- these routes are reachable by site admins only."""
    memberships = get_user_memberships_in_groups(target.id, None)
    return render_template("v1/partials/user_row.jinja",
                        user = target, memberships = memberships,
                        managed_group_ids = None,
                        groups_with_required_role = "all",
                        role_hierarchy = ROLE_HIERARCHY)


@auth_blueprint.route("/v1/users/<int:user_id>/make-admin", methods = ["GET", "POST"])
@require_auth
def make_site_admin(user_id):
    # The elevation mirror of remove_site_admin: same actor gate, same shared secret, same
    # lockout -- an admin who can't quietly remove admins must not be able to quietly mint one.
    if not current_user.site_admin:
        return _removal_refused("Only site admins can grant site admin access.")

    target = User.query.get(user_id)
    if not target:
        return _removal_refused("User not found.")

    if user_id == current_user.id:
        return _removal_refused("You cannot change your own access.")

    if target.site_admin:
        return _removal_refused(f"{target.username} is already a site admin.", "info")

    if target.status != User.STATUS_ACTIVE:
        return _removal_refused(f"{target.username}'s account is not active, so it cannot be "
                                "made a site admin.", "warning")

    if not removal_password_is_set():
        return _removal_refused("No removal password has been set. Run `flask rotate-removal-password` "
                                "on the server to set one first.", "warning")

    if request.method == "GET":
        return render_template("v1/partials/make_admin_modal.jinja", target = target)

    # POST -- lockout is checked before any bcrypt work or logging.
    if removal_locked_out(current_user):
        _log_removal_attempt(f"Blocked elevation attempt for {target.username}: locked out", target = target)
        return _removal_refused("Too many failed attempts. Try again later.")

    error = _check_admin_grant_credentials(f"elevation of {target.username}", target = target)
    if error:
        return _removal_form_error("v1/partials/make_admin_modal.jinja", error, target = target)

    assign_site_admin(target.id, assigned_by = current_user.id)
    db.session.add(UserActivity(
        user_id = current_user.id,
        activity_type = "site_admin_elevation",
        activity_target_type = "user",
        activity_target_id = target.id,
        details = f"{target.username} was made a site admin by {current_user.username}.",
        ip_address = request.remote_addr))
    db.session.commit()

    # Deliberately no email: admin-membership changes are quiet, audit-row-only events.
    flash(f"{target.username} is now a site admin.", "success")
    return _render_admin_user_row(target)


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

    name_ok, name = validate_text(request.form.get("name"), "A group name", MAX_GROUP_NAME, required=True)
    if not name_ok:
        flash(name, "danger")
        return redirect(url_for("auth.group_management"))
    desc_ok, description = validate_text(request.form.get("description"), "Description", MAX_GROUP_DESC, required=False, multiline=True)
    if not desc_ok:
        flash(description, "danger")
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
        # A non-site-admin may only attach sources they already own; otherwise a Data Owner could
        # create a group (auto-owning it), attach every source, and thereby self-grant management of
        # all sources -- letting them flip any restricted visual to public. Sources already on the
        # group that the caller can't manage are preserved so a reconcile doesn't detach them.
        if current_user.site_admin:
            attachable_ids = {s.id for s in all_sources}
        else:
            attachable_ids = {s.id for s in owned_data_sources(current_user)}
        submitted = set(current_ids - attachable_ids)
        for raw in request.form.getlist("source_ids"):
            try:
                sid = int(raw)
            except (TypeError, ValueError):
                continue
            if sid in attachable_ids:
                submitted.add(sid)
        submitted = list(submitted)

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


def _ownership_bounce():
    """Bounce to the Data Ownership page after a permission/lookup failure. The ownership routes are
    hit by HTMX requests targeting a partial, so a plain 302 would swap the whole dashboard inside
    the partial's target -- send HX-Redirect (a full-page navigation) instead; the flash stays queued
    in the session and renders on the followup page load."""
    if request.headers.get("HX-Request"):
        response = make_response("", 204)
        response.headers["HX-Redirect"] = url_for("auth.data_ownership")
        return response
    return redirect(url_for("auth.data_ownership"))


@auth_blueprint.route("/v1/groups/<int:group_id>/sources/<int:source_id>/visuals", methods=["GET", "POST"])
@require_auth
def group_source_visuals(group_id, source_id):
    # Permission here is source-ownership (Data Owner of the source / site admin), not a role in the
    # target team -- so it's checked explicitly rather than via the url-scoped require_role decorator.
    if not can_manage_source(current_user, source_id):
        flash("You do not have permission to manage visuals for this data source.", "danger")
        return _ownership_bounce()

    group = Groups.query.get(group_id)
    source = DataSources.query.get(source_id)
    if not group or not source:
        flash("Group or data source not found.", "danger")
        return _ownership_bounce()

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
        return _ownership_bounce()
    source_id = visual.data_source_id
    try:
        set_visual_visibility(visual_id, request.form.get("visibility", ""), changed_by = current_user.id)
    except ValueError as exc:
        # Validation failure (bad value / drill-hierarchy conflict): set_visual_visibility raises
        # before mutating, so flash (via the HX out-of-band swap) and re-render the section unchanged.
        flash(str(exc), "danger")
    except Exception as exc:
        # A DB/commit failure (e.g. IntegrityError) must not 500 the HTMX partial -- roll back the
        # session, log it, and re-render the section so the toggles reset to their persisted state.
        db.session.rollback()
        current_app.logger.error(f"Error setting visibility for visual {visual_id}: {exc}")
        flash("Could not update this visual's visibility. Please try again.", "danger")
    # Re-render the whole section: a change can cascade to descendant rows, not just this one.
    return render_template("v1/partials/visual_visibility_section.jinja",
                        source = DataSources.query.get(source_id),
                        visibility_rows = visibility_rows_for_source(source_id))


@auth_blueprint.route("/v1/sources/<int:source_id>/visibility", methods=["POST"])
@require_auth
def bulk_set_visibility(source_id):
    # Permission is source-ownership (Data Owner of the source / site admin), like the per-visual
    # visibility route above -- checked explicitly rather than via a role decorator.
    if not DataSources.query.get(source_id) or not can_manage_source(current_user, source_id):
        flash("You do not have permission to change this data source's visibility.", "danger")
        return _ownership_bounce()
    try:
        changed = set_source_visibility(source_id, request.form.get("visibility", ""),
                                        changed_by = current_user.id)
        if changed:
            flash(f"{changed} visual{'s' if changed != 1 else ''} set to "
                  f"{request.form.get('visibility')}.", "success")
    except ValueError as exc:
        # Validation failure (bad value): set_source_visibility raises before mutating, so flash
        # (via the HX out-of-band swap) and re-render the section unchanged.
        flash(str(exc), "danger")
    except Exception as exc:
        # A DB/commit failure must not 500 the HTMX partial -- roll back the session, log it, and
        # re-render the section so the toggles reset to their persisted state.
        db.session.rollback()
        current_app.logger.error(f"Error setting visibility for source {source_id}: {exc}")
        flash("Could not update this data source's visibility. Please try again.", "danger")
    return render_template("v1/partials/visual_visibility_section.jinja",
                        source = DataSources.query.get(source_id),
                        visibility_rows = visibility_rows_for_source(source_id))


@auth_blueprint.route("/v1/visuals/<int:visual_id>/default", methods=["POST"])
@require_auth
def set_default_visual(visual_id):
    # Permission is source-ownership (Data Owner of the visual's source / site admin), like the
    # visibility route above -- checked explicitly rather than via a role decorator.
    visual = Visuals.query.get(visual_id)
    if not visual or visual.data_source_id is None or not can_manage_source(current_user, visual.data_source_id):
        flash("You do not have permission to change this visual's default status.", "danger")
        return _ownership_bounce()
    source_id = visual.data_source_id
    try:
        set_province_default_visual(visual_id, changed_by = current_user.id)
    except ValueError as exc:
        # Validation failure (not found / not a root visual): set_province_default_visual raises
        # before mutating, so flash (via the HX out-of-band swap) and re-render the section unchanged.
        flash(str(exc), "danger")
    except Exception as exc:
        # A DB/commit failure must not 500 the HTMX partial -- roll back, log, and re-render so the
        # star resets to its persisted state.
        db.session.rollback()
        current_app.logger.error(f"Error setting default for visual {visual_id}: {exc}")
        flash("Could not update this visual's default status. Please try again.", "danger")
    # The default is one-per-province, but a province's visuals can live in several source sections,
    # so setting one default clears the star in another source's card. Re-render the posted source as
    # the swap target and every other owned source as an out-of-band swap so all cards stay in sync.
    def _section(src, oob):
        return render_template("v1/partials/visual_visibility_section.jinja",
                            source = src, visibility_rows = visibility_rows_for_source(src.id), oob = oob)
    sections = [_section(DataSources.query.get(source_id), False)]
    sections += [_section(src, True) for src in owned_data_sources(current_user) if src.id != source_id]
    return "".join(sections)


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
            jwt.decode(token, current_app.config["INVITE_JWT_SECRET"], algorithms=["HS256"])
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
        payload = jwt.decode(token, current_app.config["INVITE_JWT_SECRET"], algorithms=["HS256"])
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

    # Bind the presented token to the invite's current token. Renewing an invite mints a fresh token, so
    # a previously-issued (not-yet-expired) token must not remain usable while status is still "pending".
    if token != invite.token:
        session.pop("invite_token", None)
        flash("This invite link has been superseded. Please use the most recent invite email.", "danger")
        return redirect(url_for("auth.login"))

    # An account for this email may have been created since the invite was issued (e.g. a duplicate
    # invite accepted first). Refuse cleanly rather than 500-ing on the unique-email constraint at
    # create time.
    if User.query.filter(func.lower(User.email) == (payload.get("email") or "").lower()).first():
        session.pop("invite_token", None)
        flash("An account already exists for this email. Please log in instead.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("base.jinja", 
                            include_partials="accept invite",
                            invite=invite, 
                            email=payload.get("email"))
    
    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        # Serverside validations: username format, password strength/match, and uniqueness.
        username_ok, username = validate_username(request.form.get("username"))
        if not username_ok:
            flash(username, "danger")
            return redirect(url_for("auth.accept_invite"))
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