# Standard library imports
from functools import wraps
from datetime import datetime, timedelta, timezone
import json

# External imports
from flask import Blueprint, request, render_template, flash, current_app, redirect, url_for, session, make_response, jsonify
from bcrypt import checkpw, gensalt, hashpw
import jwt
from flask_login import login_user, current_user, logout_user
from flask_wtf.csrf import generate_csrf
from celery.result import AsyncResult
from sqlalchemy import or_, and_, func


# Internal imports
from data_viz.auth import login_manager
from data_viz.database import db
from data_viz.database.models import User, Invites, Groups, UserGroups, UserActivity, InviteGroups, DataSources, GroupDataSources, Visuals, GroupVisuals, PasswordResets
from data_viz.auth.auth_helpers import get_user_groups, get_assignable_roles, validate_password, create_user, create_group, assign_group, assign_site_admin, get_manageable_users, get_directory_users, get_user_memberships_in_groups, get_manageable_groups, set_group_data_sources, owned_data_sources, can_manage_source, teams_for_source, visuals_for_source, set_group_visuals, set_visual_visibility, set_source_visibility, set_province_default_visual, visibility_rows_for_source, landing_scopes_for, set_group_landing, set_user_landing_choice, user_starting_charts, landing_scope_label, can_open_view_page, set_user_starting_view, user_starting_views, is_last_active_site_admin, site_admin_key_is_set, check_site_admin_key, deactivate_user, set_user_password
from data_viz.auth.role_hierarchy import GROUP_ROLES, ROLE_HIERARCHY
from data_viz.auth.restricted import RestrictedSourceRefusal, blocked_group_ids, uncovered_source_ids
from data_viz.extensions import limiter
from data_viz.starting_views import PAGES as STARTING_VIEW_PAGES
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
    right after authenticating. A successful password reset counts as the same kind of boundary:
    without it, a user who trips the lockout and then uses "Forgot password?" would reset
    successfully but stay locked out immediately after."""
    cutoff = datetime.now(timezone.utc) - current_app.config["LOGIN_LOCKOUT_WINDOW"]
    query = UserActivity.query.filter(
        UserActivity.user_id == user_id,
        UserActivity.activity_type == "authentication attempt",
        UserActivity.details.like("Failed login%"),
        UserActivity.timestamp >= cutoff,
    )
    last_success = (UserActivity.query.filter(
        UserActivity.user_id == user_id,
        UserActivity.timestamp >= cutoff,
        or_(
            and_(UserActivity.activity_type == "authentication attempt",
                 UserActivity.details == "Successful login"),
            UserActivity.activity_type == "password reset",
        ),
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


# Verbatim in every flow that refuses to proceed without the shared secret.
_UNSET_KEY_MSG = ("No site admin key has been set. Run `flask rotate-site-admin-key` "
                      "on the server to set one first.")

# Deliberately does not say which of the two fields was wrong -- see _check_admin_grant_credentials.
_BAD_CREDENTIALS_MSG = "Your password or the site admin key was incorrect."


def recent_site_admin_key_failures(user_id=None):
    """Count failed site-admin-key attempts inside the lockout window (see SITE_ADMIN_KEY_LOCKOUT_*).
    With user_id, the acting admin's own failures; without, all actors' -- the secret is shared,
    so the global count bounds a brute force spread across several compromised admin accounts."""
    cutoff = datetime.now(timezone.utc) - current_app.config["SITE_ADMIN_KEY_LOCKOUT_WINDOW"]
    query = UserActivity.query.filter(
        UserActivity.activity_type == "site_admin_key_failure",
        UserActivity.timestamp >= cutoff,
    )
    if user_id is not None:
        query = query.filter(UserActivity.user_id == user_id)
    return query.count()


def site_admin_key_locked_out(user):
    if recent_site_admin_key_failures(user.id) >= current_app.config["SITE_ADMIN_KEY_LOCKOUT_THRESHOLD"]:
        return True
    return recent_site_admin_key_failures() >= current_app.config["SITE_ADMIN_KEY_LOCKOUT_GLOBAL_THRESHOLD"]


def _log_key_attempt(details, target=None, failed=False):
    """Append a UserActivity row for a site-admin-key attempt. `failed=True` marks a wrong-secret
    attempt -- the rows the brute-force lockout counts, via their dedicated activity_type. Blocked
    and informational rows keep the plain type so a lockout can never extend itself, and detail
    strings stay freely editable (nothing keys on their wording)."""
    db.session.add(UserActivity(
        user_id=current_user.id,
        activity_type="site_admin_key_failure" if failed else "site_admin_key_attempt",
        activity_target_type="user" if target else "site_admin_key",
        activity_target_id=target.id if target else None,
        details=details,
        ip_address=request.remote_addr,
    ))
    db.session.commit()


def _lockout_refusal(action_desc, target=None):
    """The shared lockout step for the protected site-admin flows: when locked out, log a
    'Blocked' row (deliberately not a failure) and return the refusal response; else None.
    Runs before any bcrypt work or failure logging."""
    if not site_admin_key_locked_out(current_user):
        return None
    _log_key_attempt(f"Blocked {action_desc}: locked out", target=target)
    return _admin_gate_refused("Too many failed attempts. Try again later.")


def _check_admin_grant_credentials(action_desc, target=None):
    """Verify the actor's own password + the shared site admin key for a protected site-admin
    membership change (removal, elevation, site-admin invites). Returns None when both
    pass; otherwise a single generic error message, having logged which field actually failed
    (for the audit trail only -- never in the user-facing message, since naming the bad field
    tells anyone with the actor's session which of the two secrets to focus on next), as a
    counted lockout row -- every path spends the same shared secret, so they all share the
    lockout."""
    own_password = request.form.get("own_password") or ""
    if not checkpw(own_password.encode("utf-8"), current_user.password_hash.encode("utf-8")):
        _log_key_attempt(f"Failed credential confirmation for {action_desc}: "
                             "incorrect account password", target=target, failed=True)
        return _BAD_CREDENTIALS_MSG
    if not check_site_admin_key(request.form.get("site_admin_key")):
        _log_key_attempt(f"Failed credential confirmation for {action_desc}: "
                             "incorrect site admin key", target=target, failed=True)
        return _BAD_CREDENTIALS_MSG
    return None


def _site_admin_grant_gate(action_desc, target=None):
    """The full gate for granting site admin access from a plain form flow (the invite page and
    the add-user modal): unset-secret guard, lockout, then both password checks. Returns None on
    success or the message to flash. The modal flows (remove/make admin) run the same pieces
    individually because each failure kind renders differently there."""
    if not site_admin_key_is_set():
        return _UNSET_KEY_MSG
    if site_admin_key_locked_out(current_user):
        _log_key_attempt(f"Blocked {action_desc}: locked out", target=target)
        return "Too many failed attempts. Try again later."
    return _check_admin_grant_credentials(action_desc, target=target)


def _render_login():
    """Render the login page as a bare partial for HTMX or the full base page otherwise."""
    if request.headers.get("HX-Request"):
        return render_template("v1/login.jinja")
    return render_template("base.jinja", include_partials="login")


def send_invite_email(invite):
    """Email the invitee their accept link. The token is a bearer credential, so it is ONLY ever sent
    over this email channel -- never flashed or logged, with one exception: under DEBUG (dev only;
    prod never sets it) the link goes to the server log INSTEAD of SES, because dev SES credentials
    are deliberately invalid and the invite flow would otherwise be untestable end-to-end. Returns
    True on success, False on failure (callers keep the invite regardless and surface a soft
    warning)."""
    base = current_app.config.get("PUBLIC_BASE_URL")
    if not base:
        current_app.logger.error("PUBLIC_BASE_URL is not set; cannot build invite link for %s", invite.email)
        return False
    accept_url = f"{base}/v1/accept-invite/{invite.token}"
    if current_app.config["DEBUG"]:
        current_app.logger.info("DEV invite link for %s: %s", invite.email, accept_url)
        return True
    subject = "You've been invited to CANASK"
    html_body = f"""
        <p>You've been invited to create an account on CANASK.</p>
        <p><a href="{accept_url}">Accept your invite</a> to set up your account.</p>
        <p>Or paste this link into your browser:<br>{accept_url}</p>
        <p>This link expires shortly for security. If it has expired, ask your administrator to resend the invite.</p>
        """
    return send_ses_email([invite.email], subject, html_body)

def _format_duration(delta):
    """Humanize a timedelta for the reset email, picking the coarsest unit that divides it
    evenly (60 -> "60 minutes", 120 -> "2 hours", 1440 -> "1 day") so PASSWORD_RESET_EXPIRY_MINUTES
    reads naturally regardless of how an operator sets it."""
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes and total_minutes % 1440 == 0:
        value, unit = total_minutes // 1440, "day"
    elif total_minutes and total_minutes % 60 == 0:
        value, unit = total_minutes // 60, "hour"
    else:
        value, unit = total_minutes, "minute"
    return f"{value} {unit}{'s' if value != 1 else ''}"


def create_password_reset(user):
    """Create a brand-new password reset row (+ JWT, activity log) for an active user, superseding
    any prior unused request for that user, and return it. This is only reached on the "user found
    and active" branch of forgot_password, so an unhandled exception here would produce a 500
    distinguishable from the identical "if an account exists..." response the rest of that route
    works hard to guarantee -- undermining its anti-enumeration design. Caller must catch."""
    PasswordResets.query.filter_by(user_id=user.id, used_at=None).update(
        {"used_at": db.func.current_timestamp()})

    token_expiry = datetime.now(timezone.utc) + current_app.config["PASSWORD_RESET_EXPIRY"]
    reset = PasswordResets.create(
        current_app.config["PASSWORD_RESET_JWT_SECRET"],
        user_id=user.id,
        expires_at=token_expiry,
        requested_ip=request.remote_addr
    )

    db.session.add(UserActivity(
        user_id=user.id,
        activity_type="password reset requested",
        activity_target_type="account",
        activity_target_id=user.id,
        details=f"Password reset requested for {user.username}",
        ip_address=request.remote_addr
    ))
    db.session.commit()
    return reset

def send_reset_email(reset, email):
    """Email the reset link. The token is a bearer credential, so it is ONLY ever sent over this
    email channel -- never flashed or logged, with one exception: under DEBUG (dev only; prod never
    sets it) the link goes to the server log INSTEAD of SES, mirroring send_invite_email. Returns
    True on success, False on failure."""
    base = current_app.config.get("PUBLIC_BASE_URL")
    if not base:
        current_app.logger.error("PUBLIC_BASE_URL is not set; cannot build reset link for %s", email)
        return False
    reset_url = f"{base}/v1/reset-password/{reset.token}"
    if current_app.config["DEBUG"]:
        current_app.logger.info("DEV password reset link for %s: %s", email, reset_url)
        return True
    subject = "CANASK password reset"
    html_body = f"""
        <p>A password reset was requested for your CANASK account.</p>
        <p><a href="{reset_url}">Reset your password</a>.</p>
        <p>Or paste this link into your browser:<br>{reset_url}</p>
        <p>This link expires in {_format_duration(current_app.config["PASSWORD_RESET_EXPIRY"])}. If you didn't request this, you can ignore this email.</p>
        """
    return send_ses_email([email], subject, html_body)


def _log_token_attempt(flow, reason, user_id=None):
    """Append a logger warning + UserActivity row for a rejected invite/reset token (invalid,
    expired, tampered, or superseded). Without this, a burst of token-guessing/probing traffic
    against either flow leaves zero trail."""
    current_app.logger.warning("%s token rejected (%s) from %s", flow, reason, request.remote_addr)
    db.session.add(UserActivity(
        user_id=user_id,
        activity_type=f"{flow}_token_rejected",
        activity_target_type="account",
        activity_target_id=user_id,
        details=reason,
        ip_address=request.remote_addr,
    ))
    db.session.commit()

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
    # user_id is User.get_id()'s "<id>:<session_version>" composite. A version mismatch means
    # the account's password changed since this session was issued (see set_user_password) --
    # treat it exactly like a deactivated account below: dead on this very next request.
    raw_id, _, version = user_id.partition(":")
    try:
        user = User.query.get(int(raw_id))
    except ValueError:
        return None
    if user and str(user.session_version) != version:
        return None
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
            # Site admins bypass all role checks, can do and access everything -- except a
            # group that is blocked for them (it holds a restricted source they are not covered
            # for; auth/restricted.py). A route scoped to ONE group by its URL is refused here,
            # so the next such route is closed by default instead of relying on its own check.
            # The "invite" and "user" sources stay open: revoking an invite and adjusting a
            # user's other groups must still be reachable, and those routes narrow their own
            # scope with _admin_blocked_group_ids().
            if current_user.site_admin:
                if group_id_source == "url" and kwargs.get("group_id") is not None \
                        and int(kwargs["group_id"]) in _admin_blocked_group_ids():
                    group = Groups.query.get(kwargs["group_id"])
                    message = _RESTRICTED_GROUP_MSG.format(name = group.name)
                    if request.headers.get("HX-Request"):
                        return _admin_gate_refused(message)
                    flash(message, "danger")
                    return redirect(url_for("main.index"))
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

# --------------------------------------------------------------------------------------- #
# Restricted data sources (auth/restricted.py): a group that holds a restricted source is
# "blocked" for a site admin who is not on that source's agreement. Their site-admin status
# gives them nothing there -- they may not add, invite, re-role or remove its members, nor
# change its sources. Everywhere below that a site admin gets "every group", these are excluded.
# --------------------------------------------------------------------------------------- #

_RESTRICTED_GROUP_MSG = ("{name} holds data covered by a data sharing agreement, so only its own "
                         "members can manage it.")

def _admin_blocked_group_ids():
    """Group ids the signed-in SITE ADMIN has no powers over. Empty for everyone else: a
    non-admin's reach is already bounded by their own group roles."""
    if not getattr(current_user, "site_admin", False):
        return set()
    return blocked_group_ids(current_user)


def _invite_touches_blocked_group(invite):
    blocked = _admin_blocked_group_ids()
    return bool(blocked) and any(ig.group_id in blocked for ig in invite.invite_groups)


def _invite_reaches_past_my_agreements(invite):
    """Whether the invite carries a group holding a restricted source the signed-in user --
    site admin OR NOT -- is not covered for. Renew and resend put the WHOLE invite back in the
    invitee's inbox, every group on it, and the role decorator admits anyone who manages just
    one of those groups: without this, a Group Admin of another group on the same invite
    could re-issue an expired invite into the agreement's group. (For a site admin this is
    exactly _invite_touches_blocked_group.)"""
    blocked = blocked_group_ids(current_user)
    return bool(blocked) and any(ig.group_id in blocked for ig in invite.invite_groups)


def _member_of_blocked_group(user):
    """Whether `user` belongs to a group that is blocked for the signed-in site admin."""
    blocked = _admin_blocked_group_ids()
    return bool(blocked) and UserGroups.query.filter(
        UserGroups.user_id == user.id, UserGroups.group_id.in_(blocked)).first() is not None


def _restricted_invite_refused(invite, action, managed_group_ids, groups_with_required_role):
    """Refuse `action` (renew / resend / adjust) on an invite into a blocked group; re-render
    its row unchanged. A non-admin manager is told only that the invite covers a group they
    do not manage: their invite row hides other groups' rows, and which group an invitee is
    headed for is not theirs to learn."""
    if current_user.site_admin:
        flash(f"This invite includes a group that holds data covered by a data sharing agreement, "
              f"so only that group's own members can {action} it.", "warning")
    else:
        flash(f"This invite also covers a group you do not manage, so you cannot {action} it. "
              "You can revoke your group's part of it and invite them again.", "warning")
    return render_template("v1/partials/invite_row.jinja",
                        invite=invite,
                        managed_group_ids=managed_group_ids,
                        groups_with_required_role=groups_with_required_role,
                        role_hierarchy=ROLE_HIERARCHY)


def _add_invite_group(invite, group_id, role):
    """The single place an InviteGroups row is persisted (the adjust modal's unsaved
    placeholders in _adjustable_groups_for aside), so the restricted-source rule has one
    backstop for invites: whatever a route's own checks did or didn't do, a site admin cannot
    put a blocked group on an invite."""
    group_id = int(group_id)   # a str id would miss the set of int ids and read as allowed
    if group_id in _admin_blocked_group_ids():
        raise RestrictedSourceRefusal(f"Site admin {current_user.username} may not invite into group "
                              f"{group_id}: it holds a restricted data source.")
    invite_group = InviteGroups(invite_id = invite.id, group_id = group_id, role = role)
    db.session.add(invite_group)
    return invite_group


def validate_group_assignments(group_assignments, groups_with_required_role):
    """Authorize a parsed {group_id: role} map against the caller's permissions.

    Returns (ok, error_message). Site admins may assign anything, except into a group blocked for
    them (see above). Otherwise the caller must manage the group (it must appear in the
    decorator-injected groups_with_required_role) and the role must be one they're allowed to grant
    there (get_assignable_roles enforces the no-outrank rule). The invite/add-user handlers must
    call this before persisting assignments -- the role decorators only prove the caller manages
    *some* group, not that the submitted group/role pairs are in scope."""
    if current_user.site_admin:
        blocked = _admin_blocked_group_ids()
        for group_id in group_assignments:
            if group_id in blocked:
                return False, _RESTRICTED_GROUP_MSG.format(name = Groups.query.get(group_id).name)
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
    invite = Invites.create(
        current_app.config["INVITE_JWT_SECRET"],
        email = email,
        status = "pending",
        expires_at = token_expiry,
        sent_by = current_user.id,
        site_admin_invite = site_admin_invite
    )

    if not site_admin_invite:
        for group_id, role in group_assignments.items():
            _add_invite_group(invite, group_id, role)

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
                func.lower(User.username) == identifier.lower(),
                func.lower(User.email) == identifier.lower(),
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
            blocked = _admin_blocked_group_ids()
            groups = [group for group in Groups.query.all() if group.id not in blocked]
            template_data["Site Wide"] = ["Site Admin"]
            for group in groups:
                template_data[group.name] = list(GROUP_ROLES)
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
        # secret as every other site-admin grant (own password + site admin key, same lockout).
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
            # An invite that already reaches a restricted-source group is not this site admin's
            # to change in any direction (add groups, or upgrade it to site admin, which strips
            # its group rows) -- only to revoke, from invite management.
            if _invite_touches_blocked_group(existing_invite):
                flash(f"A pending invite for {email} includes a group that holds data covered by "
                      "a data sharing agreement, so only that group's own members can change it.",
                      "warning")
                return redirect(url_for("auth.invite_user"))

            # Case 1: existing invite is site admin — block everything
            if existing_invite.site_admin_invite:
                flash(f"A pending invite for {email} already exists with elevated site admin permissions.", "warning")
                return redirect(url_for("auth.invite_user"))

            # Case 2: new invite is site admin — upgrade existing invite
            if site_admin_invite:
                # The flag is exclusive at acceptance time -- group rows on a flagged
                # invite are silently ignored -- so remove them instead of orphaning them.
                for ig in list(existing_invite.invite_groups):
                    db.session.delete(ig)
                existing_invite.site_admin_invite = True
                # Upgrading an existing invite is an elevation, not a fresh invite -- log it
                # under the type auditors query for elevations (matching the adjust modal).
                activity = UserActivity(
                    user_id=current_user.id,
                    activity_type="site_admin_elevation",
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

            # Case 4: add new groups to existing invite. Every submitted group is added before the
            # single activity row / commit / redirect.
            for group_id, role in group_assignments.items():
                _add_invite_group(existing_invite, group_id, role)
            group_names = ", ".join([Groups.query.get(gid).name for gid in group_assignments.keys()])
            db.session.add(UserActivity(
                user_id=current_user.id,
                activity_type="user_invite",
                activity_target_type="invite",
                activity_target_id=existing_invite.id,
                details=f"Groups {group_names} added to existing invite for {email} by {current_user.username}",
                ip_address=request.remote_addr
            ))
            db.session.commit()
            flash(f"An invite already exists for {email}; added to it: {group_names}.", "success")
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

    # The decorator only checks this for non-site-admins; a site admin can reach here with a bad id.
    # 204 would drop the flash (the OOB flash hook skips 204s); swap nothing instead so it shows.
    if invite is None:
        flash("Invite not found.", "danger")
        return "", 200, {"HX-Reswap": "none"}

    # Nobody outside a restricted source's agreement -- a site admin, or a manager of some
    # other group on the invite -- may push an invite into its group back out (they may still
    # revoke their part of it: that only removes access).
    if _invite_reaches_past_my_agreements(invite):
        return _restricted_invite_refused(invite, "resend", managed_group_ids,
                                          groups_with_required_role)

    # Resend mints nothing new -- same token, same expiry -- so site-admin invites
    # deliberately take no credential gate here (nor does renew, by the same choice: the
    # gate already approved the invite at creation).
    if invite.status != "pending":
        flash("Only pending invites can be resent.", "warning")
    elif send_invite_email(invite):
        # Re-sending re-issues a bearer credential over email, so it gets an activity row.
        db.session.add(UserActivity(
            user_id=current_user.id,
            activity_type="invite_resent",
            activity_target_type="invite",
            activity_target_id=invite.id,
            details=f"Invite for {invite.email} resent by {current_user.username}.",
            ip_address=request.remote_addr,
        ))
        db.session.commit()
        flash(f"Invite resent to {invite.email}.", "success")
    else:
        flash(f"The invite email to {invite.email} could not be sent.", "warning")

    return render_template("v1/partials/invite_row.jinja",
                        invite=invite,
                        managed_group_ids=managed_group_ids,
                        groups_with_required_role=groups_with_required_role,
                        role_hierarchy=ROLE_HIERARCHY)


@auth_blueprint.route("/v1/invites/<int:invite_id>/renew", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source = "invite", action = "renew that invite")
def renew_invite(invite_id, groups_with_required_role=None):
    invite = Invites.query.get(invite_id)
    managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())

    # Only an EXPIRED invite may be renewed: a pending invite's action is /resend (same token,
    # no new expiry), and revoked/accepted ones must stay dead so a renewal can't resurrect a
    # deliberately-cancelled invite or re-open one whose account already exists.
    if invite is None or invite.status != "expired":
        flash("This invite can no longer be renewed.", "warning")
        # 204 would drop the flash (the OOB flash hook skips 204s); swap nothing instead.
        if invite is None:
            return "", 200, {"HX-Reswap": "none"}
        return render_template("v1/partials/invite_row.jinja",
                            invite=invite,
                            managed_group_ids=managed_group_ids,
                            groups_with_required_role=groups_with_required_role,
                            role_hierarchy=ROLE_HIERARCHY)

    # Only a site admin may renew a site-admin invite: the role decorator alone would pass a
    # Group Admin who shares a group with a hybrid (flag + groups) invite. The credential gate
    # that used to sit here was removed by choice -- renewal re-sends an invite the gate already
    # approved at creation, and any site admin could mint a fresh site-admin invite outright.
    if invite.site_admin_invite and not current_user.site_admin:
        return _admin_gate_refused("Only site admins can renew a site admin invite.")
    if request.method == "GET":
        # No GET flow -- every Renew button posts directly.
        return redirect(url_for("auth.invite_management"))

    # Renewing mints a fresh token into every group on the invite. Nobody outside a restricted
    # source's agreement may do that for its group (the invitee may have left the agreement
    # since the invite expired) -- not a site admin, and not a manager of another group on
    # the same invite. Only someone covered by the agreement can.
    if _invite_reaches_past_my_agreements(invite):
        return _restricted_invite_refused(invite, "renew", managed_group_ids,
                                          groups_with_required_role)

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


def _adjustable_groups_for(invite, managed_group_ids):
    """Group rows for the adjust-permissions modal. managed_group_ids is None for site
    admins (all groups offered, missing ones as transient unsaved InviteGroups rows);
    otherwise only the invite's existing rows within the caller's managed groups."""
    adjustable_groups = []
    if managed_group_ids is None:
        blocked = _admin_blocked_group_ids()
        for group in Groups.query.all():
            if group.id in blocked:
                continue   # restricted-source group: not this site admin's to adjust
            existing_ig = next((ig for ig in invite.invite_groups if ig.group_id == group.id), None)
            if existing_ig is None:
                # If the group invite does not exist, we need one so that the site admin can add groups via the template
                existing_ig = InviteGroups(invite_id=invite.id, group_id=group.id, role=None)
                existing_ig.group = group
            adjustable_groups.append({
                "invite_group": existing_ig,
                "assignable_roles": get_assignable_roles(current_user, group.id, blocked)
            })
    else:
        for ig in invite.invite_groups:
            if ig.group_id in managed_group_ids:
                adjustable_groups.append({
                    "invite_group": ig,
                    "assignable_roles": get_assignable_roles(current_user, ig.group_id)
                })
    return adjustable_groups


@auth_blueprint.route("/v1/invites/<int:invite_id>/adjust-permissions", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source="invite", action="adjust invite permissions")
def adjust_invite_permissions(invite_id, groups_with_required_role=None):
    invite = Invites.query.get(invite_id)
    managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())

    # The decorator only checks this for non-site-admins; a site admin can reach here with a bad id.
    # 204 would drop the flash (the OOB flash hook skips 204s); swap nothing instead so it shows.
    if invite is None:
        flash("Invite not found.", "danger")
        return "", 200, {"HX-Reswap": "none"}

    # Mirror renew/resend: only a live (pending) or renewable (expired -- adjust-then-renew is a
    # legitimate flow) invite is adjustable; revoked/accepted ones stay dead.
    if invite.status not in ("pending", "expired"):
        message = "Only pending or expired invites can have their permissions adjusted."
        if request.method == "GET":
            return _admin_gate_refused(message, "warning")
        flash(message, "warning")
        return render_template("v1/partials/invite_row.jinja",
                            invite=invite,
                            managed_group_ids=managed_group_ids,
                            groups_with_required_role=groups_with_required_role,
                            role_hierarchy=ROLE_HIERARCHY)

    # A site-admin invite is only adjustable by a site admin (demote via the modal's
    # checkbox; elevate of a plain invite is gated further down). For everyone else the
    # old refusal stands -- it also covers hybrid flag+groups invites that would pass
    # the role decorator for a shared-group admin.
    if invite.site_admin_invite and not current_user.site_admin:
        message = "Site admin invites cannot be adjusted. Revoke the invite and send a new one instead."
        if request.method == "GET":
            return _admin_gate_refused(message, "warning")
        flash(message, "warning")
        return render_template("v1/partials/invite_row.jinja",
                            invite=invite,
                            managed_group_ids=managed_group_ids,
                            groups_with_required_role=groups_with_required_role,
                            role_hierarchy=ROLE_HIERARCHY)

    # An invite that reaches a restricted-source group this site admin is outside of is not
    # theirs to adjust at all -- not its other rows, and not its site-admin flag: demoting a
    # flagged invite would bring a dormant row in that group to life, elevating it would strip
    # the row. Revoke (invite management) is the one action left to them.
    if _invite_touches_blocked_group(invite):
        if request.method == "GET":
            return _admin_gate_refused(
                "This invite includes a group that holds data covered by a data sharing "
                "agreement, so only that group's own members can adjust it.", "warning")
        return _restricted_invite_refused(invite, "adjust", managed_group_ids,
                                          groups_with_required_role)

    if request.method == "GET":
        return render_template("v1/partials/adjust_permissions_modal.jinja",
                            invite=invite,
                            adjustable_groups=_adjustable_groups_for(invite, managed_group_ids))

    # Adjust the invite permissions in the DB based on the form submission
    if request.method == "POST":
        form_data = request.form.to_dict()
        managed_group_ids = None if current_user.site_admin else list(groups_with_required_role.keys())

        wants_admin = form_data.get("site_admin_invite") == "true"

        # Elevating an invite to site admin spends the shared site admin key, mirroring
        # every other grant path (invite form, make-admin, admin-invite renewal).
        if wants_admin and not invite.site_admin_invite:
            if not current_user.site_admin:
                flash("Only site admins can upgrade an invite to site admin.", "danger")
                return render_template("v1/partials/invite_row.jinja",
                                    invite=invite,
                                    managed_group_ids=managed_group_ids,
                                    groups_with_required_role=groups_with_required_role,
                                    role_hierarchy=ROLE_HIERARCHY)
            if not site_admin_key_is_set():
                return _admin_gate_refused(_UNSET_KEY_MSG, "warning")
            blocked = _lockout_refusal(f"elevation of invite for {invite.email}")
            if blocked:
                return blocked
            error = _check_admin_grant_credentials(f"elevation of invite for {invite.email}")
            if error:
                # attempted_admin keeps the switch checked and the credential fields visible on
                # the re-render, so the admin can retry instead of facing a dead form.
                return _admin_gate_form_error("v1/partials/adjust_permissions_modal.jinja", error,
                                              invite=invite, attempted_admin=True,
                                              adjustable_groups=_adjustable_groups_for(invite, managed_group_ids))
            # The flag is exclusive at acceptance time (accept_invite ignores group rows on a
            # flagged invite), so delete them rather than leaving orphans behind.
            for ig in list(invite.invite_groups):
                db.session.delete(ig)
            invite.site_admin_invite = True
            db.session.add(UserActivity(
                user_id=current_user.id,
                activity_type="site_admin_elevation",
                activity_target_type="invite",
                activity_target_id=invite.id,
                details=f"Invite for {invite.email} upgraded to site admin by {current_user.username}.",
                ip_address=request.remote_addr,
            ))
            db.session.commit()
            flash(f"Invite for {invite.email} upgraded to site admin.", "success")
            return render_template("v1/partials/invite_row.jinja",
                                invite=invite,
                                managed_group_ids=managed_group_ids,
                                groups_with_required_role=groups_with_required_role,
                                role_hierarchy=ROLE_HIERARCHY)

        # Checkbox still checked on an already-admin invite: nothing to change. Only reachable
        # by bypassing the client-side no-change gate, but say so rather than swap silently.
        if wants_admin and invite.site_admin_invite:
            flash("No changes were made.", "info")
            return render_template("v1/partials/invite_row.jinja",
                                invite=invite,
                                managed_group_ids=managed_group_ids,
                                groups_with_required_role=groups_with_required_role,
                                role_hierarchy=ROLE_HIERARCHY)

        # Demoting a site-admin invite needs no key -- any site admin can already revoke
        # it outright -- but it must land as a real group invite, so at least one valid
        # assignment is required. Validate the submitted roles BEFORE mutating anything so a
        # failed demote never touches the DB (no flag flip / row deletion to undo).
        demoting = invite.site_admin_invite and not wants_admin
        new_assignments = []
        rejected = []
        rejected_gids = set()
        # Groups holding a restricted source this site admin is outside of: a submitted role for
        # one is rejected. (The invite itself carries no such row -- refused above.)
        blocked = _admin_blocked_group_ids()
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
                    rejected_gids.add(group_id)
                    continue
                # managed_group_ids is None only for site admins (who may assign any role).
                if managed_group_ids is not None and group_id not in managed_group_ids:
                    rejected.append(group_label)
                    rejected_gids.add(group_id)
                    continue
                if group_id in blocked:
                    rejected.append(group_label)
                    rejected_gids.add(group_id)
                    continue
                # A non-site-admin may only assign roles below their own in that group; skip any the
                # caller isn't allowed to grant rather than silently elevating.
                if not current_user.site_admin and value not in get_assignable_roles(current_user, group_id):
                    rejected.append(f"{group_label} ({value})")
                    rejected_gids.add(group_id)
                    continue
                new_assignments.append((group_id, value))

        # Rows that will survive the rewrite below: rows outside the caller's scope, plus rows
        # whose submitted change was rejected (those keep their previous assignment -- the
        # "not applied" warning must not be a lie that hides a silent removal).
        kept_rows = [ig for ig in invite.invite_groups
                     if (managed_group_ids is not None and ig.group_id not in managed_group_ids)
                     or ig.group_id in rejected_gids]

        # An invite must land with at least one permission (or the admin flag): invite_user
        # refuses to create a permissionless invite, and adjustment must not produce one either
        # (its token would still mint a permissionless account, invisible to non-admin lists).
        if not new_assignments and not kept_rows:
            message = ("Assign at least one group to demote this invite from site admin."
                       if demoting else
                       "An invite needs at least one group assignment. Revoke the invite instead "
                       "if it should no longer grant anything.")
            return _admin_gate_form_error(
                "v1/partials/adjust_permissions_modal.jinja", message,
                invite=invite,
                adjustable_groups=_adjustable_groups_for(invite, managed_group_ids))

        if demoting:
            invite.site_admin_invite = False

        # Remove the permissions that were altered (rejected groups keep their existing row)
        for ig in invite.invite_groups:
            if (managed_group_ids is None or ig.group_id in managed_group_ids) \
                    and ig.group_id not in rejected_gids:
                db.session.delete(ig)
        db.session.flush()

        # Add on the new/altered permissions
        new_igs = []
        for group_id, value in new_assignments:
            new_igs.append(_add_invite_group(invite, group_id, value))
        # Rejecting an out-of-scope role is the correct RBAC outcome, but doing it silently is not --
        # tell the caller which requested changes were dropped instead of rendering pure success.
        if rejected:
            flash("Some changes were not applied (invalid role or outside your scope), and those "
                  f"groups keep their previous assignment: {', '.join(rejected)}", "warning")

        db.session.flush()

        # Log who changed what
        group_details = ", ".join([f"{Groups.query.get(ig.group_id).name} | {ig.role}" for ig in new_igs])
        activity = UserActivity(
            user_id=current_user.id,
            activity_type="invite_permissions_adjusted",
            activity_target_type="invite",
            activity_target_id=invite.id,
            details=(("Demoted from site admin invite. " if demoting else "")
                     + f"Permissions adjusted on invite for {invite.email} by {current_user.username}. New assignments: {group_details}"),
            ip_address=request.remote_addr
        )
        db.session.add(activity)
        db.session.commit()
        # Report exactly what happened: the demotion (the most consequential outcome, never
        # swallowed by per-group rejections) and precisely which assignments were applied --
        # the warning above already lists what was not.
        if demoting:
            flash(f"Invite for {invite.email} demoted from site admin.", "success")
        flash(f"Invite permissions adjusted for {invite.email}. "
              f"Applied: {group_details or 'no new assignments'}.", "success")
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

    # A group manager also gets the "All users" directory: every other active account, with
    # NO memberships attached (user_row.jinja renders such a row as email / username / status
    # plus "Add to group"), so they can find someone to add without learning who is in which
    # other group. Site admins already see everyone above.
    if not current_user.site_admin:
        listed = {row["user"].id for row in user_rows}
        for user in get_directory_users(exclude_ids = listed):
            user_rows.append({"user": user, "memberships": []})

    # The group filter: every group for a site admin, the groups they manage for anyone else
    # (the same groups the rows' badges and data-groups are limited to). Pointless with one.
    filter_groups = Groups.query.order_by(Groups.name)
    if managed_group_ids is not None:
        filter_groups = filter_groups.filter(Groups.id.in_(managed_group_ids))
    filter_groups = filter_groups.all()
    if len(filter_groups) < 2:
        filter_groups = []

    if request.headers.get("HX-Request") == "true":
        return render_template("v1/user_management.jinja",
                            user_rows = user_rows,
                            managed_group_ids = managed_group_ids,
                            filter_groups = filter_groups,
                            groups_with_required_role = groups_with_required_role,
                            role_hierarchy = ROLE_HIERARCHY)
    else:
        return render_template("base.jinja",
                            include_partials = "index",
                            dash_template = "v1/user_management.jinja",
                            user_rows = user_rows,
                            managed_group_ids = managed_group_ids,
                            filter_groups = filter_groups,
                            groups_with_required_role = groups_with_required_role,
                            role_hierarchy = ROLE_HIERARCHY)


def _may_change_own_groups():
    """Whether the signed-in user may set their OWN group roles. Only a site admin: they
    already outrank every group role, so it is no escalation, and it is the only way for an
    admin to add THEMSELVES to a group that will hold a restricted source (membership is what
    covers them -- auth/restricted.py; another covered admin or the group's own managers can
    add them too). That has to happen before the source is restricted: once the group is
    blocked for them it stays closed like for anyone they might add -- never offered, refused
    by add-user, reported as not applied by adjust-permissions, and backstopped by assign_group.
    Everyone else keeps the guardrail (a Group Admin must not re-role themselves). The site
    admin flag itself is never self-service: make_site_admin / remove_site_admin refuse it."""
    return bool(current_user.site_admin)


@auth_blueprint.route("/v1/add-user", methods=["GET", "POST"])
@require_auth
@require_role("Group Admin", group_id_source = "all_groups", action = "add users")
def add_user(groups_with_required_role = None):
    if request.method == "GET":
        template_data = {}
        if current_user.site_admin:
            blocked = _admin_blocked_group_ids()
            template_data["Site Wide"] = ["Site Admin"]
            for group in Groups.query.all():
                if group.id not in blocked:
                    template_data[group.name] = list(GROUP_ROLES)
        else:
            for group_id in groups_with_required_role.keys():
                group_obj = Groups.query.get(group_id)
                assignable_roles = get_assignable_roles(current_user, group_id)
                if assignable_roles:
                    template_data[group_obj.name] = assignable_roles

        # "Add to group" on a directory row opens this modal for that person (?user_id=). Only
        # an active account is prefilled -- the same set the directory lists.
        prefill_email = None
        raw_user_id = request.args.get("user_id", "")
        if raw_user_id.isdigit() and len(raw_user_id) <= 9:
            person = User.query.get(int(raw_user_id))
            if person and person.status == User.STATUS_ACTIVE:
                prefill_email = person.email

        # Always a modal partial — launched from the User Management page
        return render_template("v1/add_user.jinja", invitable_roles = template_data,
                               prefill_email = prefill_email)

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
        # The form never offers "Site Admin" to a non-admin. Refused here for every branch:
        # parse_group_assignments drops the group picks when it sees the flag, so for an
        # email with no account this used to fall through and send an invite carrying no
        # permissions at all.
        if site_admin_assignment and not current_user.site_admin:
            flash("Only site admins can grant site admin access.", "danger")
            return redirect(url_for("auth.user_management"))
        existing_user = User.query.filter_by(email = email).first()

        # Existing account → assign directly (no invite needed)
        if existing_user:
            # A site admin may set their OWN group roles (see _may_change_own_groups).
            if existing_user.id == current_user.id and not _may_change_own_groups():
                flash("You cannot change your own access.", "danger")
                return redirect(url_for("auth.user_management"))

            changes = 0
            if site_admin_assignment:
                if not current_user.site_admin:
                    flash("Only site admins can grant site admin access.", "danger")
                elif existing_user.site_admin:
                    flash(f"{existing_user.username} is already a site admin.", "info")
                elif existing_user.status != User.STATUS_ACTIVE:
                    # Don't point at a row action that doesn't exist for non-active accounts.
                    flash(f"{existing_user.username}'s account is deactivated and cannot be "
                          "made a site admin (there is no reactivation flow).", "warning")
                else:
                    # Elevation of an existing account goes through the protected modal flow
                    # (own password + site admin key) -- never a bare form submit.
                    flash(f"{existing_user.username} already has an account. Use the "
                          "\"Make Site Admin\" action on their row instead.", "info")
            else:
                for group_id, role in group_assignments.items():
                    group_name = Groups.query.get(group_id).name
                    # Checked for site admins too: get_assignable_roles returns every role for
                    # them EXCEPT in a group blocked by a restricted source, where it returns none.
                    if role not in get_assignable_roles(current_user, group_id):
                        if group_id in _admin_blocked_group_ids():
                            flash(_RESTRICTED_GROUP_MSG.format(name = group_name), "danger")
                        else:
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

    # Self guardrail — a manager can never act on their own membership (a site admin may:
    # see _may_change_own_groups)
    if user_id == current_user.id and not _may_change_own_groups():
        flash("You cannot change your own access.", "danger")
        return "", 200, {"HX-Reswap": "none"}

    # Groups holding a restricted source this site admin is outside of (auth/restricted.py):
    # never offered, never written, and -- because an omitted group means "revoke" in this
    # form -- never part of the scope the POST reconciles.
    blocked = _admin_blocked_group_ids()

    def in_scope(group_id, membership):
        """Groups this manager may touch for this user: site admins anything that isn't
        blocked for them, others only groups they out-rank the member's current role in."""
        if current_user.site_admin:
            return group_id not in blocked
        manager_role = groups_with_required_role.get(group_id)
        return bool(membership) and bool(manager_role) and \
            ROLE_HIERARCHY[membership.role] < ROLE_HIERARCHY[manager_role]

    if request.method == "GET":
        adjustable_groups = []
        if current_user.site_admin:
            candidate_group_ids = [g.id for g in Groups.query.all() if g.id not in blocked]
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
                "assignable_roles": get_assignable_roles(current_user, group_id, blocked)
            })

        return render_template("v1/partials/adjust_user_permissions_modal.jinja",
                            target = target, adjustable_groups = adjustable_groups)

    if request.method == "POST":
        form_data = request.form.to_dict()
        if current_user.site_admin:
            scope_group_ids = [g.id for g in Groups.query.all() if g.id not in blocked]
        else:
            scope_group_ids = [
                gid for gid in managed_group_ids
                if in_scope(gid, UserGroups.query.filter_by(user_id = user_id, group_id = gid).first())
            ]

        changed = []
        rejected = []
        # A role submitted for a group that is blocked for this site admin (a modal rendered
        # before the group became blocked, or a crafted POST) is outside the scope above: say
        # so instead of answering with an unchanged row and no explanation.
        for group_id in sorted(blocked):
            if form_data.get(f"role_{group_id}"):
                rejected.append(f"{Groups.query.get(group_id).name} (covered by a data sharing "
                                "agreement: only its own members can manage it)")
        # Grants and re-roles first, removals after. Every assign_group commits, and its
        # restricted-source backstop re-reads the signed-in admin's memberships as they then
        # are -- so a site admin moving THEMSELVES between two groups that hold the same
        # restricted source must join the new one before leaving the old one. The other order
        # drops their coverage, refuses the join, and leaves half the change applied.
        removals = []
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
                removals.append((group_id, group_name))
        for group_id, group_name in removals:
            assign_group(user_id, group_id, None, assigned_by = current_user.id, remove = True)
            changed.append(f"{group_name} removed")

        if changed:
            flash(f"Updated access for {target.username}: {', '.join(changed)}.", "success")
        # Dropped requests must be visible, not folded into a success-looking row render.
        if rejected:
            flash(f"Some changes were not applied (invalid role or outside your scope): {', '.join(rejected)}", "warning")

        memberships = get_user_memberships_in_groups(user_id, managed_group_ids)
        # A group manager who just removed a non-active account from the last group they manage
        # has no row left to show for it: "My groups" no longer includes it and the directory
        # lists active accounts only. Re-rendering would park it under "All users" with an
        # "Add to group" that cannot prefill, until the next reload -- drop the row instead.
        if managed_group_ids is not None and not memberships and target.status != User.STATUS_ACTIVE:
            return "", 200, {"HX-Reswap": "delete"}
        return render_template("v1/partials/user_row.jinja",
                            user = target, memberships = memberships,
                            managed_group_ids = managed_group_ids,
                            groups_with_required_role = groups_with_required_role,
                            role_hierarchy = ROLE_HIERARCHY)


def _admin_gate_refused(message, category = "danger"):
    """Standard refusal for the gated site-admin modal endpoints. On GET the opener button will
    show the modal no matter what, so an empty body would pop a blank shell (or stale content
    from an earlier modal) -- render the message as modal content instead. On POST: flash + swap
    nothing; the message rides back as an OOB flash (see the after_request hook)."""
    if request.method == "GET":
        return render_template("v1/partials/admin_gate_message_modal.jinja",
                               message = message, category = category)
    flash(message, category)
    return "", 200, {"HX-Reswap": "none"}


def _admin_gate_form_error(template, message, **context):
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
        return _admin_gate_refused("Only site admins can remove site admin access.")

    target = User.query.get(user_id)
    if not target:
        return _admin_gate_refused("User not found.")

    # Self guardrail, same as adjust-permissions -- and it keeps one rogue admin from
    # quietly demoting themself out of the audit trail's reach.
    if user_id == current_user.id:
        return _admin_gate_refused("You cannot remove your own site admin access.")

    if not target.site_admin:
        return _admin_gate_refused(f"{target.username} is not a site admin.", "info")

    if not site_admin_key_is_set():
        return _admin_gate_refused(_UNSET_KEY_MSG, "warning")

    if request.method == "GET":
        return render_template("v1/partials/remove_admin_modal.jinja", target = target)

    # POST -- lockout is checked before any bcrypt work or logging.
    blocked = _lockout_refusal(f"removal of {target.username}", target = target)
    if blocked:
        return blocked

    removal_action = request.form.get("removal_action")
    if removal_action not in ("demote", "deactivate"):
        # No secret was tested, so nothing is logged against the lockout.
        return _admin_gate_form_error("v1/partials/remove_admin_modal.jinja",
                                   "Choose whether to demote or deactivate the user.",
                                   target = target)

    error = _check_admin_grant_credentials(f"removal of {target.username}", target = target)
    if error:
        return _admin_gate_form_error("v1/partials/remove_admin_modal.jinja", error,
                                   target = target, selected_action = removal_action)

    if is_last_active_site_admin(target.id):
        return _admin_gate_refused(f"{target.username} is the only remaining active site admin "
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
        return _admin_gate_refused("Only site admins can grant site admin access.")

    target = User.query.get(user_id)
    if not target:
        return _admin_gate_refused("User not found.")

    if user_id == current_user.id:
        return _admin_gate_refused("You cannot change your own access.")

    if target.site_admin:
        return _admin_gate_refused(f"{target.username} is already a site admin.", "info")

    if target.status != User.STATUS_ACTIVE:
        return _admin_gate_refused(f"{target.username}'s account is not active, so it cannot be "
                                "made a site admin.", "warning")

    # A site admin who is a member of a restricted source's group has full powers over that
    # source (auth/restricted.py). So turning one of its members into a site admin is itself a
    # grant of the restricted data, and only an admin already covered by the agreement may do it.
    if _member_of_blocked_group(target):
        return _admin_gate_refused(
            f"{target.username} belongs to a group that holds data covered by a data sharing "
            "agreement. Only a site admin who is also in that group can make them a site admin.",
            "warning")

    if not site_admin_key_is_set():
        return _admin_gate_refused(_UNSET_KEY_MSG, "warning")

    if request.method == "GET":
        return render_template("v1/partials/make_admin_modal.jinja", target = target)

    # POST -- lockout is checked before any bcrypt work or logging.
    blocked = _lockout_refusal(f"elevation of {target.username}", target = target)
    if blocked:
        return blocked

    error = _check_admin_grant_credentials(f"elevation of {target.username}", target = target)
    if error:
        return _admin_gate_form_error("v1/partials/make_admin_modal.jinja", error, target = target)

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

    # Groups holding a restricted source this site admin is outside of are listed (their
    # existence isn't the secret) but offer no actions.
    blocked = _admin_blocked_group_ids()
    if request.headers.get("HX-Request") == "true":
        return render_template("v1/group_management.jinja", group_rows = group_rows,
                            blocked_group_ids = blocked)
    else:
        return render_template("base.jinja",
                            include_partials = "index",
                            dash_template = "v1/group_management.jinja",
                            group_rows = group_rows,
                            blocked_group_ids = blocked)


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

    # A group holding a restricted source this site admin is outside of never gets here:
    # require_role refuses a blocked group for every group-scoped ("url") route.

    all_sources = DataSources.query.order_by(DataSources.name).all()
    current_ids = {gds.data_source_id for gds in GroupDataSources.query.filter_by(group_id = group_id).all()}
    # Restricted sources whose agreement the caller is not on: listed (so the admin can see why
    # they can't pick it) but locked, and never attachable below.
    locked_ids = uncovered_source_ids(current_user)

    # A non-site-admin may only attach sources they already own; otherwise a Data Owner could
    # create a group (auto-owning it), attach every source, and thereby self-grant management of
    # all sources -- letting them flip any private or group-level visual to public. Sources already on the
    # group that the caller can't manage are preserved so a reconcile doesn't detach them.
    # A site admin may attach anything except a restricted source they are not covered for
    # (attaching it to a group of their choosing would be self-granting the agreement).
    if current_user.site_admin:
        attachable_ids = {s.id for s in all_sources} - locked_ids
    else:
        attachable_ids = {s.id for s in owned_data_sources(current_user)} - locked_ids

    if request.method == "GET":
        # A site admin sees the whole catalogue (an uncovered restricted source shown locked, so
        # they can see why they can't pick it). Anyone else sees only what they may hand out --
        # the sources they own -- plus whatever is already on the group, locked if it isn't
        # theirs to change: listing the rest of the catalogue with live checkboxes read as "you
        # can attach these", which the POST below has always refused.
        source_rows = []
        for s in all_sources:
            if not current_user.site_admin and s.id not in attachable_ids and s.id not in current_ids:
                continue
            if s.id in attachable_ids:
                locked = None
            elif s.id in locked_ids:
                locked = "agreement"
            else:
                locked = "not_yours"
            source_rows.append({"source": s, "checked": s.id in current_ids, "locked": locked})
        return render_template("v1/partials/group_data_sources_modal.jinja",
                            group = group, source_rows = source_rows)

    if request.method == "POST":
        submitted = set(current_ids - attachable_ids)
        for raw in request.form.getlist("source_ids"):
            try:
                sid = int(raw)
            except (TypeError, ValueError):
                continue
            if sid in attachable_ids:
                submitted.add(sid)
        submitted = list(submitted)

        # Removing a source is a two-step action. It takes the group's access to the source's
        # non-public visuals, clears its visual grants and starting charts (re-attaching brings
        # none of them back), and a Data Owner who owns the source only through this group
        # cannot attach it again. The modal asks first and sends confirm_remove=1 on the second
        # press; anything else that would detach (a stale page, a hand-built request) applies
        # NOTHING -- not even the additions in the same save, so a refused save is never half-done.
        if (current_ids - set(submitted)) and request.form.get("confirm_remove") != "1":
            flash("Nothing was changed. Removing a data source needs confirming: open "
                  "\"Manage data sources\" again, save, then confirm the removal.", "warning")
            return _render_group_row(group)

        changes = set_group_data_sources(group_id, submitted, changed_by = current_user.id)
        if changes:
            # The list says what was attached / detached and how many visual grants a detach
            # cleared -- re-attaching a source does not bring those grants back.
            flash(f"Data source access updated for {group.name}: {', '.join(changes)}.", "success")

        submitted_set = set(submitted)
        group_sources = [s for s in all_sources if s.id in submitted_set]
        return render_template("v1/partials/group_row.jinja",
                            group = group,
                            data_sources = group_sources,
                            source_count = len(group_sources),
                            blocked_group_ids = _admin_blocked_group_ids())


# --------------------------------------------------------------------------------------- #
# Starting charts: the visual a group's members open each province page on. Set per group by its
# Data Owners (it changes nothing for anyone outside the group). Any user can also set their own
# for a page, from the charts they can see (the account page, below), which beats their group's.
# Read side: visual_query.landing_for.
# --------------------------------------------------------------------------------------- #

LANDING_FIELD_PREFIX = "landing__"   # form field "landing__<scope>" -> a visual id, or "" = site default

def _render_group_row(group):
    """The Group Management row for `group`, as the group-scoped modals swap it back in."""
    source_ids = {gds.data_source_id for gds in GroupDataSources.query.filter_by(group_id = group.id).all()}
    group_sources = (DataSources.query.filter(DataSources.id.in_(source_ids))
                     .order_by(DataSources.name).all()) if source_ids else []
    return render_template("v1/partials/group_row.jinja",
                        group = group,
                        data_sources = group_sources,
                        source_count = len(group_sources),
                        blocked_group_ids = _admin_blocked_group_ids())


@auth_blueprint.route("/v1/groups/<int:group_id>/landing", methods=["GET", "POST"])
@require_auth
@require_role("Data Owner", group_id_source = "url", action = "set a group's starting charts")
def group_landing(group_id, groups_with_required_role = None):
    # A group holding a restricted source this site admin is outside of never gets here:
    # require_role refuses a blocked group for every group-scoped ("url") route.
    group = Groups.query.get(group_id)
    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("auth.group_management"))

    if request.method == "GET":
        scopes = landing_scopes_for(group_id)
        source_names = dict(DataSources.query.with_entities(DataSources.id, DataSources.name).all())
        for scope in scopes:
            # Candidates grouped by data source, for the <optgroup>s (first-appearance order).
            by_source = {}
            for visual in scope["candidates"]:
                by_source.setdefault(source_names.get(visual.data_source_id, "Other"), []).append(visual)
            scope["by_source"] = list(by_source.items())
        return render_template("v1/partials/group_landing_modal.jinja", group = group,
                            scopes = scopes, field_prefix = LANDING_FIELD_PREFIX)

    picks = {}
    for field, raw in request.form.items():
        if not field.startswith(LANDING_FIELD_PREFIX):
            continue
        raw = raw.strip()
        # isascii() too: str.isdigit() is true for characters such as "\u00b2" that int() rejects.
        if raw and not (raw.isascii() and raw.isdigit()):
            flash("That isn't a chart this group can use.", "danger")
            return _render_group_row(group)
        picks[field[len(LANDING_FIELD_PREFIX):]] = int(raw) if raw else None
    try:
        changes = set_group_landing(group_id, picks, changed_by = current_user.id)
        if changes:
            flash(f"Starting charts updated for {group.name}: {'; '.join(changes)}.", "success")
    except ValueError as exc:
        # Validation failure (a visual the group's members can't all see): set_group_landing
        # raises before writing anything, so flash and re-render the row unchanged.
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error setting starting charts for group {group_id}: {exc}")
        flash("Could not update this group's starting charts. Please try again.", "danger")
    return _render_group_row(group)


@auth_blueprint.route("/v1/landing-choice/<scope>", methods=["POST"])
@require_auth
def landing_choice(scope):
    """The signed-in user's own starting chart for `scope`: `visual_id` = a top-level visual they
    can see there, or "" to go back to the default. Two callers: the select on their account page
    (HTMX -> the re-rendered section, with the outcome as a flash) and the one-time notice on a
    province page (a plain fetch in visualGeneration.js -> JSON)."""
    from_page = request.headers.get("HX-Request") == "true"
    error, opens_on = None, None
    if "visual_id" not in request.form:
        # Only an explicit empty value means "back to the default"; a bare POST is a mistake.
        error, status = "No chart was chosen.", 400
    else:
        raw = request.form.get("visual_id", "").strip()
        try:
            opens_on = set_user_landing_choice(current_user, scope, raw or None)
        except ValueError as exc:
            error, status = str(exc), 400
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(f"Error saving landing choice for {scope}: {exc}")
            error, status = "Could not save your choice. Please try again.", 500

    if not from_page:
        return (jsonify({"error": error}), status) if error else jsonify({"default": opens_on})
    # htmx does not swap a 4xx/5xx body, so the account page always gets 200 + the section as
    # it now stands; a refusal rides out as the flash.
    context = _starting_charts_context()
    if error:
        flash(error, "danger")
    else:
        row = next((r for r in context["scopes"] if r["province"] == scope), None)
        opened = next((c for c in row["charts"] if c.name == opens_on), None) if row else None
        flash(f"Saved. {landing_scope_label(scope)} will open on "
              f"{(opened.menu_name or opened.name) if opened else 'its default chart'}.", "success")
    return render_template("v1/partials/starting_charts_section.jinja", **context)


def _starting_charts_context():
    """What the Starting charts section renders: the chart pages (`scopes`) and the two pages
    that take a saved view instead (`views`)."""
    return {"scopes": user_starting_charts(current_user), "views": user_starting_views(current_user)}


@auth_blueprint.route("/v1/starting-view/<page>", methods=["POST"])
@require_auth
def starting_view(page):
    """Save or clear the signed-in user's starting view for a page whose view lives in the
    address (Confluence, the DAS Explorer). `query` = the page's current query string (its own
    "Save as my starting view" button, a plain fetch -> JSON); `link` = a link pasted on the
    account page; `clear` = remove it (both HTMX -> the re-rendered section + a flash)."""
    from_account = request.headers.get("HX-Request") == "true"

    def _answer(error = None, status = 400, message = None):
        if not from_account:
            return (jsonify({"error": error}), status) if error else jsonify({"saved": True})
        flash(error or message, "danger" if error else "success")
        return render_template("v1/partials/starting_charts_section.jinja", **_starting_charts_context())

    if page not in STARTING_VIEW_PAGES:
        return ("", 404) if not from_account else _answer("That page doesn't have a starting view.")
    label = STARTING_VIEW_PAGES[page]["label"]
    if not can_open_view_page(current_user, page):
        return _answer(f"You don't have access to the {label} page.", status = 403)
    try:
        if "clear" in request.form:
            set_user_starting_view(current_user, page, None)
            return _answer(message = f"Cleared. {label} will open on its usual view.")
        raw = request.form.get("link") if "link" in request.form else request.form.get("query")
        set_user_starting_view(current_user, page, raw or "")
    except ValueError as exc:
        return _answer(str(exc))
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error saving starting view for {page}: {exc}")
        return _answer("Could not save that. Please try again.", status = 500)
    return _answer(message = f"Saved. {label} will open on that view.")


# --------------------------------------------------------------------------------------- #
# "My account": one tab per option. Each tab is its own URL, so it can be linked to (the province
# page's notice links to starting-charts) and only the open tab's content is computed -- the
# starting-charts list checks every page the user can open. A new option is an ACCOUNT_TABS entry,
# a v1/partials/account_<key with _>.jinja partial, and its context in _account_context.
# --------------------------------------------------------------------------------------- #
ACCOUNT_TABS = (
    ("info", "Account info"),
    ("starting-charts", "Starting charts"),
    ("password", "Password"),
    ("sign-ins", "Sign-in activity"),
)

# What the "Sign-in activity" tab lists, newest first: the account's own sign-in and security
# events. Everything else in UserActivity (group changes, admin actions...) is not for this page.
ACCOUNT_ACTIVITY_TYPES = ("authentication attempt", "logout", "password reset",
                          "username_changed", "email_change_requested", "email_changed")
ACCOUNT_ACTIVITY_LIMIT = 20

def _account_activity_label(row):
    """A plain description of one UserActivity row for its own user. Never the raw `details`: a
    failed sign-in's details quote whatever was typed into the username box, which is often a
    password pasted into the wrong field."""
    details = row.details or ""
    if row.activity_type == "authentication attempt":
        if details == "Successful login":
            return "Signed in"
        if "wrong current password" in details:
            return "Wrong password entered on the account page"
        if details.startswith("Failed login"):
            return "Failed sign-in attempt"
        if "blocked" in details.lower():
            return "Sign-in blocked after too many failed attempts"
        if details.startswith("Login refused"):
            return "Sign-in refused"
        return "Sign-in attempt"
    if row.activity_type == "password reset":
        return "Password changed" if "changed" in details.lower() else "Password reset by emailed link"
    return {"logout": "Signed out",
            "username_changed": "Username changed",
            "email_change_requested": "Email change requested",
            "email_changed": "Email changed"}.get(row.activity_type, row.activity_type)

def _account_context(tab):
    context = {"tabs": ACCOUNT_TABS, "active_tab": tab}
    if tab == "info":
        # At the cap, the username form is switched off and says when it opens up again.
        wait = _username_change_wait(current_user)
        context["username_wait"] = _about(wait) if wait is not None else None
        context["memberships"] = (UserGroups.query.filter_by(user_id = current_user.id)
                                  .join(Groups, Groups.id == UserGroups.group_id).order_by(Groups.name).all())
    elif tab == "starting-charts":
        context.update(_starting_charts_context())
    elif tab == "sign-ins":
        rows = (UserActivity.query.filter(UserActivity.user_id == current_user.id,
                                          UserActivity.activity_type.in_(ACCOUNT_ACTIVITY_TYPES))
                .order_by(UserActivity.timestamp.desc(), UserActivity.id.desc())
                .limit(ACCOUNT_ACTIVITY_LIMIT).all())
        context["activity"] = [{"when": row.timestamp, "what": _account_activity_label(row),
                                "ip": row.ip_address} for row in rows]
        context["activity_limit"] = ACCOUNT_ACTIVITY_LIMIT
    return context

def _render_account(tab):
    """The account page on `tab`: the bare partial for HTMX (tab links and the forms all swap
    #account-div), the full shell otherwise."""
    context = _account_context(tab)
    if request.headers.get("HX-Request") == "true":
        return render_template("v1/account.jinja", **context)
    return render_template("base.jinja", include_partials = "index",
                        dash_template = "v1/account.jinja", **context)

@auth_blueprint.route("/v1/account", methods=["GET"])
@auth_blueprint.route("/v1/account/<tab>", methods=["GET"])
@require_auth
def account(tab = "info"):
    """"My account" for the signed-in user: "info" (who they are and their groups, with the
    username and email forms; roles are changed by a group's managers on User Management),
    "starting-charts", "password" and "sign-ins"."""
    if tab not in dict(ACCOUNT_TABS):
        return redirect(url_for("auth.account"))
    return _render_account(tab)


def _confirm_own_password(action):
    """Re-check the signed-in user's password before a sensitive account change (`action`
    completes "trying to ..."). Returns None when it is right, else the message to show.

    A wrong guess is logged exactly like a failed sign-in ("Failed login..."), so it counts
    toward the same per-account lockout: someone sitting in a stolen session gets no more
    guesses at the password here than at the login form."""
    if login_locked_out(current_user):
        _log_auth_attempt(current_user, current_user.username,
                          f"Blocked while trying to {action}: too many recent failed attempts")
        return "Too many failed attempts. Please wait a few minutes and try again."
    password = request.form.get("current_password") or ""
    if not checkpw(password.encode("utf-8"), current_user.password_hash.encode("utf-8")):
        _log_auth_attempt(current_user, current_user.username,
                          f"Failed login: wrong current password while trying to {action}")
        return "Your current password is incorrect."
    return None


@auth_blueprint.route("/v1/account/password", methods=["POST"])
@limiter.limit(lambda: current_app.config["RATELIMIT_LOGIN"])
@require_auth
def account_change_password():
    new_password = request.form.get("new_password") or ""
    error = _confirm_own_password("change their password")
    if error is None:
        if new_password != (request.form.get("confirm_password") or ""):
            error = "The new passwords do not match."
        else:
            valid, message = validate_password(new_password)
            if not valid:
                error = message
            elif checkpw(new_password.encode("utf-8"), current_user.password_hash.encode("utf-8")):
                error = "Your new password must be different from your current one."
    if error:
        flash(error, "danger")
        return _render_account("password")

    user = current_user._get_current_object()
    try:
        set_user_password(user, new_password, ip_address = request.remote_addr,
                          details = f"Password changed by {user.username} from their account page")
    except Exception:
        flash("We couldn't change your password just now. Please try again.", "danger")
        return _render_account("password")
    # set_user_password bumps session_version, which signs out EVERY session for the account,
    # this one included. Sign this one straight back in under the new version.
    login_user(user)
    flash("Your password has been changed. You've been signed out everywhere else.", "success")
    return _render_account("password")


def _about(delta):
    """A duration for a sentence, rounded UP so "you can try again in ..." is never early:
    "about 4 hours", "about 1 hour", "about 25 minutes"."""
    seconds = max(delta.total_seconds(), 0)
    if seconds > 3600:
        hours = -(-int(seconds) // 3600)
        return f"about {hours} hours"
    if seconds > 45 * 60:
        return "about 1 hour"
    minutes = max(-(-int(seconds) // 60), 1)
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"

def _username_change_wait(user):
    """How long until `user` may change their username again (a timedelta), or None if they may
    now: at most USERNAME_CHANGE_LIMIT successful changes per USERNAME_CHANGE_WINDOW, counted from
    their `username_changed` activity rows. Attempts that were refused (taken, invalid) never
    wrote a row, so they don't count here -- the route's per-IP limit covers those."""
    limit = current_app.config["USERNAME_CHANGE_LIMIT"]
    window = current_app.config["USERNAME_CHANGE_WINDOW"]
    now = datetime.now(timezone.utc).replace(tzinfo = None)   # the column is naive UTC
    recent = (UserActivity.query.filter(UserActivity.user_id == user.id,
                                        UserActivity.activity_type == "username_changed",
                                        UserActivity.timestamp >= now - window)
              .order_by(UserActivity.timestamp.desc(), UserActivity.id.desc()).limit(limit).all())
    if len(recent) < limit:
        return None
    # The oldest of the changes that fill the allowance is the one that frees a slot first.
    return max(recent[-1].timestamp + window - now, timedelta(seconds = 1))

def _username_cap_text():
    """"2 times in the last 24 hours", from the config."""
    limit = current_app.config["USERNAME_CHANGE_LIMIT"]
    hours = int(current_app.config["USERNAME_CHANGE_WINDOW"].total_seconds() // 3600)
    return f"{limit} time{'s' if limit != 1 else ''} in the last {hours} hour{'s' if hours != 1 else ''}"


@auth_blueprint.route("/v1/account/username", methods=["POST"])
@limiter.limit(lambda: current_app.config["RATELIMIT_USERNAME_CHANGE"])
@require_auth
def account_change_username():
    user = current_user._get_current_object()
    valid, username = validate_username(request.form.get("username"))
    wait = _username_change_wait(user)
    if not valid:
        flash(username, "danger")
    elif username == user.username:
        flash("That is already your username.", "info")
    elif wait is not None:
        flash(f"You've changed your username {_username_cap_text()}. "
              f"You can change it again in {_about(wait)}.", "warning")
    elif User.query.filter(func.lower(User.username) == username.lower(), User.id != user.id).first():
        flash("Username already taken. Please choose a different username.", "danger")
    else:
        old = user.username
        user.username = username
        db.session.add(UserActivity(
            user_id = user.id,
            activity_type = "username_changed",
            activity_target_type = "account",
            activity_target_id = user.id,
            details = f"Username changed from {old} to {username}",
            ip_address = request.remote_addr))
        try:
            db.session.commit()
            flash(f"Your username is now {username}.", "success")
        except Exception as exc:
            # The unique index is the real guard: two people can pass the check above at once.
            db.session.rollback()
            current_app.logger.error(f"Error changing username for user {user.id}: {exc}")
            flash("Username already taken. Please choose a different username.", "danger")
    return _render_account("info")


def _email_change_token(user, new_email):
    """A signed, self-contained request to change `user`'s email to `new_email`. It is only good
    while the account still has the email AND the session_version it was issued under: confirming
    it (or changing the password) moves one of those, so a link can't be used twice, and two
    pending links can't both win. Same signing key as password resets; the "purpose" claim keeps
    the two kinds of link from standing in for each other."""
    return jwt.encode({
        "purpose": "email_change",
        "user_id": user.id,
        "old_email": user.email,
        "new_email": new_email,
        "sv": user.session_version,
        "exp": (datetime.now(timezone.utc) + current_app.config["PASSWORD_RESET_EXPIRY"]).timestamp(),
    }, current_app.config["PASSWORD_RESET_JWT_SECRET"], algorithm = "HS256")

def _read_email_change_token(token):
    """(user, new_email, None) for a link that can still be confirmed, else (None, None, message).
    Every rejection is logged (_log_token_attempt)."""
    expired = "This confirmation link has expired. Please request the change again."
    invalid = "This confirmation link is not valid."
    stale = "This confirmation link is no longer valid. Please request the change again."
    try:
        payload = jwt.decode(token, current_app.config["PASSWORD_RESET_JWT_SECRET"], algorithms = ["HS256"])
    except jwt.ExpiredSignatureError:
        _log_token_attempt("email_change", "expired")
        return None, None, expired
    except jwt.InvalidTokenError:
        _log_token_attempt("email_change", "invalid or tampered")
        return None, None, invalid
    if payload.get("purpose") != "email_change":
        _log_token_attempt("email_change", "wrong token purpose")
        return None, None, invalid
    user = User.query.get(payload.get("user_id")) if payload.get("user_id") else None
    new_email = payload.get("new_email")
    if user is None or not new_email:
        _log_token_attempt("email_change", "unknown account")
        return None, None, invalid
    if user.status != User.STATUS_ACTIVE:
        _log_token_attempt("email_change", "account not active", user_id = user.id)
        return None, None, stale
    if (payload.get("sv") != user.session_version
            or (payload.get("old_email") or "").lower() != user.email.lower()):
        _log_token_attempt("email_change", "superseded", user_id = user.id)
        return None, None, stale
    if User.query.filter(func.lower(User.email) == new_email.lower(), User.id != user.id).first():
        _log_token_attempt("email_change", "address now in use", user_id = user.id)
        return None, None, stale
    return user, new_email, None

def _send_account_email(to_address, subject, html_body, dev_note):
    """Send one account email. Under DEBUG (dev only) it goes to the server log INSTEAD of SES,
    like the invite and reset links."""
    if current_app.config["DEBUG"]:
        current_app.logger.info("DEV %s for %s", dev_note, to_address)
        return True
    return send_ses_email([to_address], subject, html_body)


@auth_blueprint.route("/v1/account/email", methods=["POST"])
# Same budget as the forgot-password form: per-IP, plus the site-wide SES cost ceiling.
@limiter.limit(lambda: current_app.config["RATELIMIT_PASSWORD_RESET"])
@limiter.limit(lambda: current_app.config["RATELIMIT_PASSWORD_RESET_GLOBAL"],
               key_func = lambda: "password-reset-global")
@require_auth
def account_request_email_change():
    """Step one of changing your email: confirm your password, then a link goes to the NEW
    address. The email on the account does not change until that link is confirmed
    (account_confirm_email), so a typo can't cut you off from password resets."""
    user = current_user._get_current_object()
    valid, new_email = validate_email(request.form.get("new_email"), required = True)
    if not valid:
        flash(new_email, "danger")
        return _render_account("info")
    error = _confirm_own_password("change their email")
    if error:
        flash(error, "danger")
        return _render_account("info")
    if new_email.lower() == user.email.lower():
        flash("That is already your email address.", "info")
        return _render_account("info")

    # An address that belongs to another account gets the SAME answer and no email: saying "that
    # one is taken" would let any signed-in user test who has an account. (The link would be
    # refused at confirmation anyway.)
    taken = User.query.filter(func.lower(User.email) == new_email.lower(), User.id != user.id).first()
    sent = None
    if not taken:
        base = current_app.config.get("PUBLIC_BASE_URL")
        if not base:
            current_app.logger.error("PUBLIC_BASE_URL is not set; cannot build email-change link for %s", user.username)
            sent = False
        else:
            link = f"{base}/v1/account/confirm-email/{_email_change_token(user, new_email)}"
            sent = _send_account_email(
                new_email, "Confirm your new CANASK email address",
                f"""
                <p>Someone signed in to the CANASK account <strong>{user.username}</strong> asked to
                change its email address to this one.</p>
                <p><a href="{link}">Confirm the new email address</a>.</p>
                <p>Or paste this link into your browser:<br>{link}</p>
                <p>This link expires in {_format_duration(current_app.config["PASSWORD_RESET_EXPIRY"])}. If you
                didn't ask for this, ignore this email and nothing will change.</p>
                """,
                f"email-change link: {link}")
            if not sent:
                current_app.logger.error("Failed to send email-change confirmation for user %s", user.id)
    outcome = ("address already in use, nothing sent" if taken
               else "confirmation sent" if sent else "confirmation could not be sent")
    db.session.add(UserActivity(
        user_id = user.id,
        activity_type = "email_change_requested",
        activity_target_type = "account",
        activity_target_id = user.id,
        details = f"Email change requested to {new_email} ({outcome})",
        ip_address = request.remote_addr))
    db.session.commit()
    flash(f"We've sent a confirmation link to {new_email}. Your email stays {user.email} until "
          f"you open it. If it doesn't arrive, check the address and try again.", "info")
    return _render_account("info")


@auth_blueprint.route("/v1/account/confirm-email/<token>", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["RATELIMIT_PASSWORD_RESET_SUBMIT"],
               exempt_when = lambda: request.method == "GET")
def account_confirm_email(token):
    """Step two: the link from the new mailbox. No sign-in needed (it is often opened on another
    device) -- the link itself is the proof. GET only shows a confirm button, because mail
    scanners open links; the change is made on POST."""
    def _leave():
        target = url_for("auth.account") if current_user.is_authenticated else url_for("auth.login")
        if request.headers.get("HX-Request"):
            return "", 204, {"HX-Redirect": target}   # the flash renders on that page
        return redirect(target)

    user, new_email, error = _read_email_change_token(token)
    if error:
        flash(error, "danger")
        return _leave()
    if request.method == "GET":
        return render_template("base.jinja", include_partials = "index",
                            dash_template = "v1/confirm_email.jinja",
                            token = token, account_user = user, new_email = new_email)

    old_email = user.email
    user.email = new_email
    # Bumping the version is what makes the link single-use and kills any other pending link
    # (_read_email_change_token). It also signs the account out everywhere, which is right for a
    # change to how you sign in; this session is signed back in below if it is theirs.
    user.session_version = (user.session_version or 0) + 1
    db.session.add(UserActivity(
        user_id = user.id,
        activity_type = "email_changed",
        activity_target_type = "account",
        activity_target_id = user.id,
        details = f"Email changed from {old_email} to {new_email}",
        ip_address = request.remote_addr))
    was_signed_in_as_them = current_user.is_authenticated and current_user.id == user.id
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error changing email for user {user.id}: {exc}")
        flash("We couldn't change your email just now. Please request the change again.", "danger")
        return _leave()
    if was_signed_in_as_them:
        login_user(user)
    # Tell the address that just lost the account, in case its owner did not ask for this.
    notified = _send_account_email(
        old_email, "Your CANASK email address was changed",
        f"""
        <p>The email address on the CANASK account <strong>{user.username}</strong> was changed
        from this address to <strong>{new_email}</strong>.</p>
        <p>If you made this change, you don't need to do anything.</p>
        <p>If you didn't, contact a CANASK site administrator right away.</p>
        """,
        f"email-changed notice (now {new_email})")
    if not notified:
        current_app.logger.error("Failed to send email-changed notice to the old address of user %s", user.id)
    flash(f"Your email address is now {new_email}.", "success")
    return _leave()


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
    # Visual grants only go to teams that have the source (the page only ever offers those);
    # without this a source's owner could grant its visuals to any group id.
    if group_id not in {team.id for team in teams_for_source(source_id)}:
        flash(f"{group.name} does not have access to {source.name}. Give the group the data "
              "source first.", "danger")
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
    # The site-wide default is one per province across every data source, so it is a site-admin
    # setting: a Data Owner sees only their own sources and must not be able to move the page off
    # another owner's visual. Owners choose what their OWN group opens on instead (group_landing).
    if not current_user.site_admin:
        flash("Only site admins can change a page's site-wide default. To set what a page opens "
              "on for your own group, go to Group Management → Starting charts.", "danger")
        return _ownership_bounce()
    # The admin must still be able to manage the visual's source (a restricted source they are
    # outside of is closed to them) -- checked explicitly rather than via a role decorator.
    visual = Visuals.query.get(visual_id)
    if not visual or visual.data_source_id is None or not can_manage_source(current_user, visual.data_source_id):
        flash("You do not have permission to change this visual's default status.", "danger")
        return _ownership_bounce()
    source_id = visual.data_source_id
    try:
        set_province_default_visual(visual_id, changed_by = current_user.id)
    except ValueError as exc:
        # Validation failure (not found / not a root visual / the current default is on a source
        # this admin is outside of): set_province_default_visual raises before mutating, so flash
        # (via the HX out-of-band swap) and re-render the section unchanged.
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
            _log_token_attempt("invite", "expired")
            flash("This invite link has expired.", "danger")
            return redirect(url_for("auth.login"))
        except jwt.InvalidTokenError:
            _log_token_attempt("invite", "invalid or tampered")
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
        _log_token_attempt("invite", "expired")
        flash("This invite link has expired.", "danger")
        return redirect(url_for("auth.login"))
    except jwt.InvalidTokenError:
        session.pop("invite_token", None)
        _log_token_attempt("invite", "invalid or tampered")
        flash("The invite link is invalid.", "danger")
        return redirect(url_for("auth.login"))

    invite = Invites.query.get(payload.get("invite_id"))
    if not invite or invite.status != "pending":
        session.pop("invite_token", None)
        _log_token_attempt("invite", "no longer pending", user_id=invite.sent_by if invite else None)
        flash("This invite is no longer valid.", "danger")
        return redirect(url_for("auth.login"))

    # Bind the presented token to the invite's current token. Renewing an invite mints a fresh token, so
    # a previously-issued (not-yet-expired) token must not remain usable while status is still "pending".
    if token != invite.token:
        session.pop("invite_token", None)
        _log_token_attempt("invite", "superseded", user_id=invite.sent_by)
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
        if User.query.filter(func.lower(User.username) == username.lower()).first():
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

@auth_blueprint.route("/v1/forgot-password", methods=["GET", "POST"])
# Mirrors the feedback dual-limit (main.py): per-IP throttle plus a global SES-cost ceiling that
# only deducts on a response that actually completes the flow, so a flood of reCAPTCHA-failing
# or malformed-email requests can't exhaust the budget. GET is unlimited. Unlike /feedback (whose
# success path IS the 200), this route's success path is the 302 to login -- the malformed-email
# and reCAPTCHA-failure branches both re-render the form with a 200, so gating on 200 would still
# let those exhaust the budget.
@limiter.limit(lambda: current_app.config["RATELIMIT_PASSWORD_RESET"],
               exempt_when=lambda: request.method == "GET")
@limiter.limit(lambda: current_app.config["RATELIMIT_PASSWORD_RESET_GLOBAL"],
               key_func=lambda: "password-reset-global",
               deduct_when=lambda r: r.status_code == 302,
               exempt_when=lambda: request.method == "GET")
def forgot_password():
    if current_user.is_authenticated:
        flash("You are already logged in.", "info")
        return redirect(url_for("main.index"))

    def _render_forgot_password():
        if request.headers.get("HX-Request"):
            return render_template("v1/forgot_password.jinja")
        return render_template("base.jinja", include_partials="forgot password")

    if request.method == "GET":
        return _render_forgot_password()

    # POST: format validation and reCAPTCHA failures re-render the form with a specific error --
    # neither one reveals whether the address has an account. Every other outcome below (address
    # found/not found, active/invited/deactivated, send success/failure) MUST reach the exact same
    # flash + redirect so existence can't be inferred from the response.
    ok, email = validate_email(request.form.get("email"), required=True)
    if not ok:
        flash(email, "danger")
        return _render_forgot_password()

    recaptcha_ok, _ = verify_recaptcha(request.form.get("recaptcha-token"), "forgot_password")
    if not recaptcha_ok:
        flash("Could not verify you're human. Please try again.", "danger")
        return _render_forgot_password()

    user = User.query.filter(func.lower(User.email) == email.lower()).first()

    if user and user.status == User.STATUS_ACTIVE:
        try:
            reset = create_password_reset(user)
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating password reset for {user.email}: {str(e)}")
            reset = None
        if reset:
            sent_ok = send_reset_email(reset, user.email)
            if not sent_ok:
                current_app.logger.error("Failed to send password reset email to %s", user.email)
                db.session.add(UserActivity(
                    user_id=user.id,
                    activity_type="password reset email failed",
                    activity_target_type="account",
                    activity_target_id=user.id,
                    details=f"Password reset email failed to send for {user.username}",
                    ip_address=request.remote_addr
                ))
                db.session.commit()
    else:
        # No account, or one that can't use a reset link (invited/deactivated): do nothing, but
        # still pay roughly the same JWT-signing cost as the real-send branch above -- not a claim
        # that this fully closes the timing gap (the real branch also pays 2-3 DB round trips plus,
        # outside DEBUG, a synchronous SES call), just a cheap step in that direction (the analogue
        # of _DUMMY_PASSWORD_HASH's checkpw in login()).
        jwt.encode({"purpose": "password_reset", "user_id": 0, "reset_id": 0, "exp": 0},
                   current_app.config["PASSWORD_RESET_JWT_SECRET"], algorithm="HS256")

    # Identical regardless of found/not-found, active/invited/deactivated, or send success/failure
    # (see the docstring above) -- a conditional "we couldn't send it" message here would itself be
    # an enumeration oracle (only a real active account ever reaches the send step), so any
    # send/DB failure is surfaced to the user in this SAME generic copy, never a distinct one.
    flash("If an account exists for that address, a password reset link has been sent. If you "
          "don't receive it shortly, please try again, and contact an administrator if the "
          "issue persists.", "info")
    return redirect(url_for("auth.login"))

@auth_blueprint.route("/v1/reset-password", methods=["GET", "POST"])
@auth_blueprint.route("/v1/reset-password/<token>", methods=["GET"])
# The route that actually writes the new password; consistent with login/feedback/forgot-password,
# all of which throttle their write step. The POST requires a valid session-held token, so this is
# defense in depth rather than the primary control.
@limiter.limit(lambda: current_app.config["RATELIMIT_PASSWORD_RESET_SUBMIT"],
               exempt_when=lambda: request.method == "GET")
def reset_password(token=None):
    if current_user.is_authenticated:
        flash("You are already logged in.", "warning")
        return redirect(url_for("main.index"))

    def _render_reset_password(email):
        if request.headers.get("HX-Request"):
            return render_template("v1/reset_password.jinja", email=email)
        return render_template("base.jinja", include_partials="reset password", email=email)

    # Handle token from URL
    if token:
        try:
            jwt.decode(token, current_app.config["PASSWORD_RESET_JWT_SECRET"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            _log_token_attempt("password_reset", "expired")
            flash("This password reset link has expired. Please request a new one.", "danger")
            return redirect(url_for("auth.login"))
        except jwt.InvalidTokenError:
            _log_token_attempt("password_reset", "invalid or tampered")
            flash("This password reset link is invalid.", "danger")
            return redirect(url_for("auth.login"))
        # Keeps the bearer token out of the address bar/Referer for the POST.
        session["password_reset_token"] = token
        return redirect(url_for("auth.reset_password"))

    # Get and decode token from session
    token = session.get("password_reset_token")
    if not token:
        flash("No password reset token provided.", "danger")
        return redirect(url_for("auth.login"))

    try:
        payload = jwt.decode(token, current_app.config["PASSWORD_RESET_JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        session.pop("password_reset_token", None)
        _log_token_attempt("password_reset", "expired")
        flash("This password reset link has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.login"))
    except jwt.InvalidTokenError:
        session.pop("password_reset_token", None)
        _log_token_attempt("password_reset", "invalid or tampered")
        flash("This password reset link is invalid.", "danger")
        return redirect(url_for("auth.login"))

    # The "purpose" claim is what blocks an invite JWT (which lacks it) from being presented here,
    # even when both tokens are signed with the same fallback SECRET_KEY.
    if payload.get("purpose") != "password_reset":
        session.pop("password_reset_token", None)
        _log_token_attempt("password_reset", "wrong token purpose")
        flash("This password reset link is invalid.", "danger")
        return redirect(url_for("auth.login"))

    reset = PasswordResets.query.get(payload.get("reset_id"))
    if not reset or not reset.is_valid:
        session.pop("password_reset_token", None)
        _log_token_attempt("password_reset", "used, expired, or missing row",
                            user_id=reset.user_id if reset else payload.get("user_id"))
        flash("This password reset link is no longer valid. Please request a new one.", "danger")
        return redirect(url_for("auth.login"))

    # Bind the presented token to the reset row's current token. A newer request for the same user
    # supersedes this one (see create_password_reset), so a previously-issued (not-yet-expired)
    # token must not remain usable once it has been superseded.
    if token != reset.token:
        session.pop("password_reset_token", None)
        _log_token_attempt("password_reset", "superseded", user_id=reset.user_id)
        flash("This password reset link has been superseded. Please use the most recent email.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.get(reset.user_id)
    if not user or user.status != User.STATUS_ACTIVE:
        # Same generic message as the used/expired/missing-row branch above -- don't reveal that
        # the account state changed since the link was issued.
        session.pop("password_reset_token", None)
        flash("This password reset link is no longer valid. Please request a new one.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return _render_reset_password(user.email)

    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.reset_password"))
        valid, message = validate_password(password)
        if not valid:
            flash(message, "danger")
            return redirect(url_for("auth.reset_password"))

        # Deliberately no comparison against the current password hash: checking (or messaging on)
        # whether the new password matches the old one would leak information about the old
        # password to whoever holds this token.
        if not reset.claim():
            # Lost the race to a concurrent submission of the same token -- see PasswordResets.claim.
            session.pop("password_reset_token", None)
            flash("This password reset link is no longer valid. Please request a new one.", "danger")
            return redirect(url_for("auth.login"))
        try:
            set_user_password(user, password, ip_address=request.remote_addr)
        except Exception:
            # set_user_password already rolled back and logged -- that also un-claims the token
            # above (same transaction), so keep the session token and let the user retry.
            flash("We couldn't reset your password just now. Please try again. If it keeps "
                  "happening, email spencer.fietz@ucalgary.ca.", "danger")
            return redirect(url_for("auth.reset_password"))

        session.pop("password_reset_token", None)
        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for("auth.login"))