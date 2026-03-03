# External Imports
from flask_login import UserMixin

# Internal Imports
from data_viz.database import db

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key = True)
    email = db.Column(db.String(255), unique = True, nullable = False)
    username = db.Column(db.String(255), unique = True, nullable = False)
    password_hash = db.Column(db.String(255), nullable = False)
    status = db.Column(db.String(50), default = "invited")
    site_admin = db.Column(db.Boolean, default = False)
    invited_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = True)
    def __repr__(self):
        return f"<User {self.username}>"
    
class Groups(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key = True)
    name = db.Column(db.String(255), unique = True, nullable = False)
    description = db.Column(db.String(255), nullable = True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = False)
    created_at = db.Column(db.DateTime, nullable = False, default = db.func.current_timestamp())

    def __repr__(self):
        return f"<Group {self.name}>"

class UserGroups(db.Model):
    __tablename__ = "user_groups"
    __table_args__ = (db.UniqueConstraint("user_id", "group_id", name = "uq_user_group"),)

    id = db.Column(db.Integer, primary_key = True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable = False)
    role = db.Column(db.String(255), nullable = False, default = "member")

    def __repr__(self):
        return f"<UserGroup User ID: {self.user_id}, Group ID: {self.group_id}>"

class Invites(db.Model):
    __tablename__ = "invites"

    id = db.Column(db.Integer, primary_key = True)
    email = db.Column(db.String(255), nullable = False)
    status = db.Column(db.String(50), default = "pending")
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable = False)
    role = db.Column(db.String(255), nullable = False, default = "member")
    token = db.Column(db.String(255), unique = True, nullable = False)
    expires_at = db.Column(db.DateTime, nullable = False)
    sent_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = False)

    def __repr__(self):
        return f"<Invite {self.email} to Group ID: {self.group_id}>"

class Visuals(db.Model):
    __tablename__ = "visuals"

    id = db.Column(db.Integer, primary_key = True)
    name = db.Column(db.String(255), nullable = False)
    about = db.Column(db.String(5000), nullable = True)
    province = db.Column(db.String(255), nullable = False)
    vis_type = db.Column(db.String(255), nullable = False)
    data_types = db.Column(db.String(255), nullable = False)
    menu_name = db.Column(db.String(255), nullable = False)
    menu_parent = db.Column(db.String(255), nullable = False)
    level = db.Column(db.String(255), nullable = False)
    next_vis = db.Column(db.ForeignKey("visuals.id"), nullable = True)
    previous_vis = db.Column(db.ForeignKey("visuals.id"), nullable = True)

    def __repr__(self):
        return f"<Visual {self.name}>"

class GroupVisuals(db.Model):
    __tablename__ = "group_visuals"

    id = db.Column(db.Integer, primary_key = True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable = False)
    visual_id = db.Column(db.Integer, db.ForeignKey("visuals.id"), nullable = False)

    def __repr__(self):
        return f"<GroupVisual Group ID: {self.group_id}, Visual ID: {self.visual_id}>"

class DataSources(db.Model):
    __tablename__ = "data_sources"

    id = db.Column(db.Integer, primary_key = True)
    name = db.Column(db.String(255), nullable = False)
    link = db.Column(db.String(255), nullable = False)
    last_updated = db.Column(db.DateTime, nullable = False)
    data_until = db.Column(db.DateTime, nullable = False)

    def __repr__(self):
        return f"<DataSource {self.name}>"

class DataPoints(db.Model):
    __tablename__ = "data_points"
    __table_args__ = (db.Index("idx_datapoints_visual_geo_year", "geo", "time_frame_type", "data_metric"),)

    id = db.Column(db.Integer, primary_key = True)
    data_source_id = db.Column(db.Integer, db.ForeignKey("data_sources.id"), nullable = False)
    geo_type = db.Column(db.String(255), nullable = False)
    geo = db.Column(db.String(255), nullable = False)
    time_frame_type = db.Column(db.String(255), nullable = False)
    time_frame = db.Column(db.String(255), nullable = False)
    data_metric = db.Column(db.String(255), nullable = False)
    data_value = db.Column(db.Float, nullable = False)

    def __repr__(self):
        return f"<DataPoint Geo: {self.geo}, Time Frame: {self.time_frame}, Data Metric: {self.data_metric}, Data Value: {self.data_value}>"

class VisualQuery(db.Model):
    __tablename__ = "visual_queries"

    id = db.Column(db.Integer, primary_key = True)
    filter_type = db.Column(db.String(255), nullable = False)
    filter_value = db.Column(db.String(255), nullable = False)
    for_visual_id = db.Column(db.Integer, db.ForeignKey("visuals.id"), nullable = False)

    def __repr__(self):
        return f"<VisualQuery ID: {self.id}, Filter Type: {self.filter_type}, Filter Value: {self.filter_value}, For Visual ID: {self.for_visual_id}>"

class UserActivity(db.Model):
    __tablename__ = "user_activity"

    id = db.Column(db.Integer, primary_key = True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = True)
    activity_type = db.Column(db.String(255), nullable = False)
    activity_target_type = db.Column(db.String(255), nullable = True)
    activity_target_id = db.Column(db.Integer, nullable = True)
    details = db.Column(db.String(5000), nullable = True)
    timestamp = db.Column(db.DateTime, nullable = False, default = db.func.current_timestamp())
    ip_address = db.Column(db.String(255), nullable = True)

    def __repr__(self):
        return f"<UserActivity User ID: {self.user_id}, Activity Type: {self.activity_type}, Timestamp: {self.timestamp}>"