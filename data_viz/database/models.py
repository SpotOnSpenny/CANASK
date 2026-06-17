# External Imports
from flask_login import UserMixin
import jwt

# Internal Imports
from data_viz.database import db

# Allowed values for Visuals.visibility (per-visual access level). Ordered most- to least-restrictive.
VISUAL_VISIBILITY = ("private", "group", "public")

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

    user = db.relationship("User", foreign_keys = [user_id])
    group = db.relationship("Groups", foreign_keys = [group_id])

    def __repr__(self):
        return f"<UserGroup User ID: {self.user_id}, Group ID: {self.group_id}>"

class Invites(db.Model):
    __tablename__ = "invites"

    id = db.Column(db.Integer, primary_key = True)
    email = db.Column(db.String(255), nullable = False)
    token = db.Column(db.String(512), nullable = True)
    status = db.Column(db.String(50), default = "pending")
    sent_at = db.Column(db.DateTime, nullable = False, default = db.func.current_timestamp())
    expires_at = db.Column(db.DateTime, nullable = False)
    expiry_task_id = db.Column(db.String(255), nullable = True)
    sent_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = False)
    site_admin_invite = db.Column(db.Boolean, default = False)

    # Relationships
    sent_by_user = db.relationship("User", foreign_keys = [sent_by])
    invite_groups = db.relationship("InviteGroups", backref="invite", lazy = True)

    def __repr__(self):
        return f"<Invite {self.email}, Status: {self.status}>"

    def generate_jwt(self, secret_key):
        payload = {
            "email": self.email,
            "invite_id": self.id,
            "exp": self.expires_at.timestamp()
        }
        self.token = jwt.encode(payload, secret_key, algorithm="HS256")
        return self.token

class InviteGroups(db.Model):
    __tablename__ = "invite_groups"
    __tableargs__ = (db.UniqueConstraint("invite_id", "group_id", name = "uq_invite_group"))

    id = db.Column(db.Integer, primary_key = True)
    invite_id = db.Column(db.Integer, db.ForeignKey("invites.id"), nullable = False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable = False)
    role = db.Column(db.String(255), nullable = False, default = "member")

    # Relationships
    group = db.relationship("Groups", foreign_keys = [group_id])

    def __repr__(self):
        return f"<InviteGroup Invite ID: {self.invite_id} for Group ID {self.group_id} with Role {self.role}>"

class Visuals(db.Model):
    __tablename__ = "visuals"

    id = db.Column(db.Integer, primary_key = True)
    name = db.Column(db.String(255), nullable = False)
    about = db.Column(db.String(5000), nullable = True)
    province = db.Column(db.String(255), nullable = False)
    vis_type = db.Column(db.String(255), nullable = False)
    data_types = db.Column(db.String(255), nullable = True)
    menu_name = db.Column(db.String(255), nullable = True)
    menu_parent = db.Column(db.String(255), nullable = True)
    level = db.Column(db.String(255), nullable = True)
    next_vis = db.Column(db.ForeignKey("visuals.id"), nullable = True)
    previous_vis = db.Column(db.ForeignKey("visuals.id"), nullable = True)
    # Data layer: links a visual to its source + stores the cleaned-data presentation config used to
    # reconstruct the frontend JSON shape from the normalized DataPoints rows (see visual_query.py).
    data_source_id = db.Column(db.Integer, db.ForeignKey("data_sources.id"), nullable = True)
    visual_options = db.Column(db.JSON, nullable = True)
    data_shape = db.Column(db.String(50), nullable = True)
    # Menu/presentation config served to the frontend (DB-driven; formerly the static visuals.js).
    # next_vis_name / vis_parent_name are string visual_ids for the drill chain (per-province).
    chart_type = db.Column(db.String(50), nullable = True)
    next_vis_name = db.Column(db.String(255), nullable = True)
    vis_parent_name = db.Column(db.String(255), nullable = True)
    is_default = db.Column(db.Boolean, nullable = True, default = False)
    # Per-visual access level, set by Data Owners on the Data Ownership panel (see VISUAL_VISIBILITY):
    #   private -> site admins + the source's Data Owners only
    #   group   -> signed-in members of a group granted this visual (GroupVisuals)
    #   public  -> anyone, no sign-in required
    visibility = db.Column(db.String(20), nullable = False, server_default = "private", default = "private")
    # Self-describing query definition (Stage 2): how to select + shape this visual's DataPoints,
    # so the read path no longer needs visual_specs.VISUAL_SPECS. `metric` is the event, `geo_type`
    # the geo granularity, `dimension_type`/`dimension2_type` the disaggregator columns the facts
    # carry, and `drill_chain` (JSON list) the dimension nesting order used to render/drill.
    metric = db.Column(db.String(255), nullable = True)
    geo_type = db.Column(db.String(255), nullable = True)
    dimension_type = db.Column(db.String(255), nullable = True)
    dimension2_type = db.Column(db.String(255), nullable = True)
    drill_chain = db.Column(db.JSON, nullable = True)
    # How the (dimension, dimension2) values compose into a series label/key
    # (constant | suffix_y | plain | sex_substance | manner_substance) -- lets the generic read path
    # and the client-side adapter build series without VISUAL_SPECS.
    key_kind = db.Column(db.String(50), nullable = True)

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
    link = db.Column(db.String(255), nullable = True)
    last_updated = db.Column(db.DateTime, nullable = True)
    data_until = db.Column(db.DateTime, nullable = True)
    # Free-text "about" blurb and the latest scrape's display strings (the pipeline stores month-granular
    # values like "March, 2025" that don't fit a DateTime), used when reconstructing a visual's data_source block.
    about = db.Column(db.Text, nullable = True)
    last_updated_str = db.Column(db.String(255), nullable = True)
    data_until_str = db.Column(db.String(255), nullable = True)

    def __repr__(self):
        return f"<DataSource {self.name}>"

class GroupDataSources(db.Model):
    __tablename__ = "group_data_sources"
    __table_args__ = (db.UniqueConstraint("group_id", "data_source_id", name = "uq_group_data_source"),)

    id = db.Column(db.Integer, primary_key = True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable = False)
    data_source_id = db.Column(db.Integer, db.ForeignKey("data_sources.id"), nullable = False)

    group = db.relationship("Groups", foreign_keys = [group_id])
    data_source = db.relationship("DataSources", foreign_keys = [data_source_id])

    def __repr__(self):
        return f"<GroupDataSource Group ID: {self.group_id}, Data Source ID: {self.data_source_id}>"

class DataPoints(db.Model):
    __tablename__ = "data_points"
    __table_args__ = (db.Index("idx_datapoints_metric_geo_year", "data_source_id", "data_metric", "geo", "time_frame"),)

    id = db.Column(db.Integer, primary_key = True)
    data_source_id = db.Column(db.Integer, db.ForeignKey("data_sources.id"), nullable = False)
    geo_type = db.Column(db.String(255), nullable = False)
    geo = db.Column(db.String(255), nullable = False)
    time_frame_type = db.Column(db.String(255), nullable = False)
    time_frame = db.Column(db.String(255), nullable = False)
    # Semantic, cross-visual quantity (e.g. "opioid_deaths", "samples"), the unit, and up to two generic
    # disaggregation dimensions (e.g. sex/Male, age_group/20-29; regional uses both for drug_category + result).
    data_metric = db.Column(db.String(255), nullable = False)
    data_type = db.Column(db.String(50), nullable = False)
    dimension_type = db.Column(db.String(255), nullable = True)
    dimension_value = db.Column(db.String(255), nullable = True)
    dimension2_type = db.Column(db.String(255), nullable = True)
    dimension2_value = db.Column(db.String(255), nullable = True)
    # Numeric value for querying; data_value_text preserves the original string cell so reconstruction
    # round-trips the exact JSON type (the source mixes "47" strings and real numbers).
    data_value = db.Column(db.Float, nullable = True)
    data_value_text = db.Column(db.String(255), nullable = True)

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