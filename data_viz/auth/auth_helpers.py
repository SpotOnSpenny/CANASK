# Standard Library Imports
import re

# External Imports
from bcrypt import hashpw, gensalt
from flask import flash, has_request_context

# Internal Imports
from data_viz.database import db
from data_viz.database.models import User, Invites, UserGroups, Groups, UserActivity
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY

def create_user(email, username, password, invited_by = None, status = "active",site_admin = False):
    password_hash = hashpw(password.encode('utf-8'), gensalt()).decode('utf-8')
    user = User(
        email = email,
        username = username, 
        password_hash = password_hash,
        status = status, 
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

def create_group(name, created_by, description = None):
    group = Groups(
        name = name,
        description = description,
        created_by = created_by
    )

    db.session.add(group)
    db.session.commit()

    created_by_user = User.query.get(created_by)
    activity = UserActivity(
        user_id = created_by,
        activity_type = "creation",
        activity_target_type = "group",
        activity_target_id = group.id,
        details = f"Group {name} created by {created_by_user.username}."
    )

    db.session.add(activity)
    db.session.commit()
    return group

def assign_group(user_id, group_id, role, assigned_by = None, remove = False):
    existing_membership = UserGroups.query.filter_by(user_id = user_id, group_id = group_id).first()
    user = User.query.get(user_id)
    group = Groups.query.get(group_id)
    assigner = User.query.get(assigned_by) if assigned_by else None

    try:
        if remove and not existing_membership:
            raise ValueError("User does not belong to the specified group. Unable to remove non-existent membership.")
        if remove and existing_membership:
            db.session.delete(existing_membership)
            db.session.commit()
            if assigner:
                details = f"User {user.username} removed from Group {group.name} by {assigner.username}." 
            else:
                details = f"User ID {user_id} removed from Group ID {group_id} without assigner."
            activity = UserActivity(
                user_id = user_id,
                activity_type = "group_removal",
                activity_target_type = "user", 
                details = details
            )
            
            db.session.add(activity)
            db.session.commit()
            return None
    except Exception as e:
        print(f"Error in assign_group: {e}")
        if has_request_context():
            flash(f"An error occured while removing group access: {str(e)}", "danger")
        raise e

    try:
        if not remove and existing_membership and existing_membership.role == role:
            raise ValueError("User already has the specified role in this group.")

        if existing_membership and not remove:
            existing_membership.role = role
            membership = existing_membership
        else:
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
            details = f"User ID {user_id} assigned to Group ID {group_id} with role {role} without assigner."
        activity = UserActivity(
            user_id = user_id,
            activity_type = "group_assignment",
            activity_target_type = "user", 
            details = details
        )
    except Exception as e:
        print(f"Error in assign_group: {e}")
        if has_request_context():
            flash(f"An error occured while assigning group access: {str(e)}", "danger")
        raise e
    
    db.session.add(activity)
    db.session.add(membership)
    db.session.commit()
    return membership

def assign_site_admin(user_id, remove = False, assigned_by = None):
    user = User.query.get(user_id)
    if not user:
        raise ValueError("User not found. Unable to assign site admin role.")

    user.site_admin = not remove

    if assigned_by:
        assigner = User.query.get(assigned_by)
        details = f"User {user.username} was granted site admin privileges by {assigner.username}."
    else:
        details = f"User ID {user_id} was granted site admin privileges without assigner."

    activity = UserActivity(
        user_id = user_id,
        activity_type = "site_admin_assignment",
        activity_target_type = "user", 
        details = details
    )

    db.session.add(activity)
    db.session.add(user)
    db.session.commit()
    return user

def get_user_groups(user):
    if user.site_admin:
        return Groups.query.all()
    else:
        memberships = UserGroups.query.filter(
            UserGroups.user_id == user.id
        ).all()

        group_ids = [membership.group_id for membership in memberships]
        return Groups.query.filter(Groups.id.in_(group_ids)).all()

def get_assignable_roles(user, group_id):
    if user.site_admin:
        return ["Data Owner", "Group Admin", "Member"]

    print(user, group_id)
    membership = UserGroups.query.filter_by(user_id = user.id, group_id = group_id).first()
    if membership:
        return [role for role in ROLE_HIERARCHY.keys() if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[membership.role]]
    else:
        return []

def validate_password(password):
    if len(password) < 12:
        return False, "Password must be at least 12 characters long."
    if not any(char.isupper() for char in password):
        return False, "Password must contain at least one uppercase letter."
    special_characters = re.findall(r'[^a-zA-Z0-9]', password)
    if not special_characters:
        return False, "Password must contain at least one special character."
    return True, "Password is valid."