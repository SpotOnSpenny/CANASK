# External Imports
from bcrypt import hashpw, gensalt

# Internal Imports
from data_viz.database import db
from data_viz.models import User, Invites, UserGroups, Groups, UserActivity
from role_hierarchy import ROLE_HIERARCHY

def create_user(email, username, password, invited_by = None, site_admin = False):
    password_hash = hashpw(password.encode('utf-8'), gensalt()).decode('utf-8')
    user = User(
        email = email,
        username = username, 
        password_hash = password_hash,
        status = "active", 
        invited_by = invited_by,
        site_admin = site_admin
    )

    db.session.add(user)
    db.session.commit()

    if invited_by:
        invite = Invites.query.filter_by(email = email, status = "pending").first()
        if invite:
            invite.status = "accepted"
            db.session.add(invite)
        inviter = User.query.get(invited_by)
        details = f"Account created for {username} with email {email} with invite from {inviter.username}."
    else:
        details = f"Account created for {username} with email {email} without invite."

    activity = UserActivity(
        user_id = user.id,
        activity_type = "creation",
        activity_target_type = "account",
        details = details
    )

    db.session.add(activity)
    db.session.commit()
    return user

def assign_group(user_id, group_id, role, assigned_by = None):
    membership = UserGroups(
        user_id = user_id, 
        group_id = group_id,
        role = role, 
    )

    user = User.query.get(user_id)
    group = Groups.query.get(group_id)
    if assigned_by:
        assigner = User.query.get(assigned_by)
        details = f"User {user.username} assigned to Group {group.name} with role {role} by {assigner.username}." 
    else:
        details = f"User ID {user_id} assigned to Group ID {group_id} with role {role} without asigner."
    activity = UserActivity(
        user_id = user_id,
        activity_type = "group_assignment",
        activity_target_type = "user", 
        details = details
    )
    
    db.session.add(activity)
    db.session.add(membership)
    db.session.commit()
    return membership

def get_user_groups(user):
    if user.site_admin:
        return Groups.query.all()
    else:
        invitable_roles = ["group_admin", "data_owner"]
        memberships = UserGroups.query.filter(
            UserGroups.user_id == user.id,
            UserGroups.role.in_(invitable_roles)
        ).all()

        group_ids = [membership.group_id for membership in memberships]
        return Groups.query.filter(Groups.id.in_(group_ids)).all()

def get_assignable_roles(user, group_id):
    if user.site_admin:
        return ["site_admin", "data_owner", "group_admin", "member"]

    membership = UserGroups.query.filter_by(user_id = user.id, group_id = group_id).first()
    if membership:
        return [role for role in ROLE_HIERARCHY.keys() if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[membership.role]]
    else:
        return []