# External Imports
from flask_login import UserMixin
import jwt

# Internal Imports
from data_viz.database import db

# Allowed values for Visuals.visibility (per-visual access level). Ordered most- to least-restrictive.
VISUAL_VISIBILITY = ("private", "group", "public")

class User(UserMixin, db.Model):
    __tablename__ = "users"

    # Account lifecycle values for `status`. Use these constants, not string literals -- nothing
    # constrains the column to this set, so a typo ("Active") would silently make an account
    # locked-out or never-lockable.
    STATUS_INVITED = "invited"
    STATUS_ACTIVE = "active"
    STATUS_DEACTIVATED = "deactivated"

    id = db.Column(db.Integer, primary_key = True)
    email = db.Column(db.String(255), unique = True, nullable = False)
    username = db.Column(db.String(255), unique = True, nullable = False)
    password_hash = db.Column(db.String(255), nullable = False)
    status = db.Column(db.String(50), default = STATUS_INVITED)
    site_admin = db.Column(db.Boolean, default = False)
    invited_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = True)

    @property
    def is_active(self):
        # Flask-Login authentication gate. UserMixin.is_active is always True; override it so only
        # active accounts may log in AND keep an existing session. The kill-switch for existing
        # sessions is explicit: load_user returns None for inactive users and require_auth re-checks
        # is_active (Flask-Login 0.6's UserMixin.is_authenticated also returns is_active, but the
        # guarantee shouldn't hinge on that mixin subtlety). create_user and the bootstrap/seed paths
        # all set STATUS_ACTIVE, so this does not affect normal accounts.
        return self.status == self.STATUS_ACTIVE

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

class PasswordResets(db.Model):
    __tablename__ = "password_resets"

    id = db.Column(db.Integer, primary_key = True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = False)
    token = db.Column(db.String(512), nullable = True)
    created_at = db.Column(db.DateTime, nullable = False, default = db.func.current_timestamp())
    expires_at = db.Column(db.DateTime, nullable = False)
    used_at = db.Column(db.DateTime, nullable = True)
    requested_ip = db.Column(db.String(255), nullable = True)

    user = db.relationship("User", foreign_keys = [user_id])

    def generate_jwt(self, secret_key):
        # "purpose" + distinct claim names keep this token non-interchangeable with
        # invite JWTs, which may share a signing key via the SECRET_KEY fallback.
        payload = {
            "purpose": "password_reset",
            "user_id": self.user_id,
            "reset_id": self.id,
            "exp": self.expires_at.timestamp(),
        }
        self.token = jwt.encode(payload, secret_key, algorithm="HS256")
        return self.token

class Visuals(db.Model):
    __tablename__ = "visuals"
    # Defense-in-depth for the access-control invariant: visibility must be one of VISUAL_VISIBILITY.
    # The UI write path (set_visual_visibility) already validates, but _can_see fails closed on any
    # unrecognized value, so a stray write would silently hide a visual -- the DB constraint blocks it.
    __table_args__ = (
        db.CheckConstraint(
            "visibility IN (" + ", ".join(f"'{v}'" for v in VISUAL_VISIBILITY) + ")",
            name = "ck_visuals_visibility"),
    )

    id = db.Column(db.Integer, primary_key = True)
    name = db.Column(db.String(255), nullable = False)
    about = db.Column(db.String(5000), nullable = True)
    province = db.Column(db.String(255), nullable = False)
    # URL slug for deep-linking straight to a visual (/v1/province/<province>/<slug>). Authored in the
    # manifests (auto-derived from visual_id when omitted). Expected to be unique per province, but this
    # is NOT DB-enforced -- a manifest `slug` override could collide, and the deep-link lookup in
    # main.py resolves to the first match. Keep manifest slugs distinct per province (or add a
    # UniqueConstraint("province","slug") + a backfilling migration if collisions become a risk).
    slug = db.Column(db.String(255), nullable = True)
    vis_type = db.Column(db.String(255), nullable = False)
    data_types = db.Column(db.String(255), nullable = True)
    menu_name = db.Column(db.String(255), nullable = True)
    menu_parent = db.Column(db.String(255), nullable = True)
    level = db.Column(db.String(255), nullable = True)
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
    # Derived state: computed by visual_definitions.derive_drill_chain (from shape + dimension2_type),
    # never authored in the manifest -- re-derived on every define-visuals, so don't hand-edit it.
    drill_chain = db.Column(db.JSON, nullable = True)
    # How the (dimension, dimension2) values compose into a series label/key
    # (constant | suffix_y | plain | sex_substance | manner_substance) -- lets the generic read path
    # and the client-side adapter build series without VISUAL_SPECS.
    key_kind = db.Column(db.String(50), nullable = True)

    def __repr__(self):
        return f"<Visual {self.name}>"

class GroupVisuals(db.Model):
    __tablename__ = "group_visuals"
    __table_args__ = (db.UniqueConstraint("group_id", "visual_id", name = "uq_group_visual"),)

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

class SiteAdminKey(db.Model):
    __tablename__ = "site_admin_key"

    # Single-row table holding the bcrypt hash of the shared "site admin key" required for every
    # site-admin membership change (elevation, removal, site-admin invites and their renewal). The
    # row is absent until the first `flask rotate-site-admin-key` run -- "never set" is a legal
    # state all the gated routes refuse on.
    id = db.Column(db.Integer, primary_key = True)
    password_hash = db.Column(db.String(255), nullable = False)
    updated_at = db.Column(db.DateTime, nullable = False, default = db.func.current_timestamp())
    # NULL = rotated via the break-glass CLI (no acting web user).
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable = True)

    def __repr__(self):
        return f"<SiteAdminKey updated {self.updated_at}>"

# --- Drug Analysis Service (DAS) row-level tables -------------------------------------------------
# The DAS Explorer serves raw sample rows (paginated/filtered server-side), not aggregated facts, so
# it gets its own tables instead of the DataPoints star schema. Ingested by `flask ingest-das` from
# the monthly nationalDAS workbook; history accumulates across files, keyed by sample number.

class DasDrugCodes(db.Model):
    __tablename__ = "das_drug_codes"

    # Natural key from the workbook's "Drug Id" sheet. Codes referenced by sample sheets but missing
    # from the lookup get placeholder rows (english_name = code) so the FKs below always hold.
    code = db.Column(db.String(255), primary_key = True)
    # english_name is the source's full name, which can embed synonym lists after semicolons
    # ("Psilocybin;(3-[2-(dimethylamino)ethyl]-...)"); display_name is its primary segment (fallback:
    # the code), computed at ingest -- what the explorer table and pivot labels show.
    display_name = db.Column(db.String(255), nullable = True)
    english_name = db.Column(db.String(500), nullable = True)
    french_name = db.Column(db.String(500), nullable = True)
    english_legal_name = db.Column(db.String(500), nullable = True)
    french_legal_name = db.Column(db.String(500), nullable = True)
    pharm_class = db.Column(db.String(255), nullable = True)
    pharm_subclass = db.Column(db.String(255), nullable = True)
    cas = db.Column(db.String(255), nullable = True)
    act = db.Column(db.String(255), nullable = True)
    schedule = db.Column(db.String(255), nullable = True)
    item = db.Column(db.String(255), nullable = True)

    def __repr__(self):
        return f"<DasDrugCode {self.code}: {self.english_name}>"

class DasSamples(db.Model):
    __tablename__ = "das_samples"

    sample_number = db.Column(db.String(255), primary_key = True)
    public_health = db.Column(db.Boolean, nullable = True)
    contains_nps = db.Column(db.Boolean, nullable = True)
    date_received = db.Column(db.Date, nullable = True, index = True)
    date_returned = db.Column(db.Date, nullable = True, index = True)
    city = db.Column(db.String(255), nullable = True)
    province = db.Column(db.String(50), nullable = True, index = True)
    description = db.Column(db.Text, nullable = True)
    # Denormalized "Psilocybin; Fentanyl" list of resolved drug names -- the table's display/substring-
    # filter column. The per-drug rows live in das_sample_drugs for pivot joins.
    drugs_identified = db.Column(db.Text, nullable = True)
    # Which monthly file the row came from (the filename's data-until month), for provenance.
    source_month = db.Column(db.Date, nullable = True, index = True)

    def __repr__(self):
        return f"<DasSample {self.sample_number} ({self.province})>"

class DasSampleDrugs(db.Model):
    __tablename__ = "das_sample_drugs"
    __table_args__ = (db.UniqueConstraint("sample_number", "position", name = "uq_das_sample_drug_position"),)

    id = db.Column(db.Integer, primary_key = True)
    sample_number = db.Column(db.String(255),
                              db.ForeignKey("das_samples.sample_number", ondelete = "CASCADE"),
                              nullable = False)
    drug_code = db.Column(db.String(255), db.ForeignKey("das_drug_codes.code"), nullable = False, index = True)
    position = db.Column(db.Integer, nullable = False)   # Drug ID 1..20 column the code came from

    def __repr__(self):
        return f"<DasSampleDrug {self.sample_number} #{self.position}: {self.drug_code}>"

class DasQuant(db.Model):
    __tablename__ = "das_quant"

    id = db.Column(db.Integer, primary_key = True)
    # Not an FK -- quantitation sample numbers aren't guaranteed to appear in the ID All sheet. And
    # no (sample, drug) unique constraint: one sample can carry multiple quantitations of one drug
    # (e.g. different units). Idempotency is by source_month -- each monthly file owns its month.
    sample_number = db.Column(db.String(255), nullable = False, index = True)
    public_health = db.Column(db.Boolean, nullable = True)
    date_received = db.Column(db.Date, nullable = True)
    date_returned = db.Column(db.Date, nullable = True)
    city = db.Column(db.String(255), nullable = True)
    province = db.Column(db.String(50), nullable = True, index = True)
    description = db.Column(db.Text, nullable = True)
    drug_code = db.Column(db.String(255), db.ForeignKey("das_drug_codes.code"), nullable = True)
    quantity = db.Column(db.Float, nullable = True)
    units = db.Column(db.String(50), nullable = True)
    source_month = db.Column(db.Date, nullable = True, index = True)

    def __repr__(self):
        return f"<DasQuant {self.sample_number}: {self.drug_code} {self.quantity} {self.units}>"

class DasNps(db.Model):
    __tablename__ = "das_nps"

    id = db.Column(db.Integer, primary_key = True)
    # Uncertified findings all share the literal sample number "N/A*", so there is no usable unique
    # key here; idempotency is by source_month, like das_quant.
    sample_number = db.Column(db.String(255), nullable = False, index = True)
    drug_code = db.Column(db.String(255), db.ForeignKey("das_drug_codes.code"), nullable = True)
    substance_name = db.Column(db.String(500), nullable = True)
    other_name = db.Column(db.Text, nullable = True)
    province = db.Column(db.String(50), nullable = True, index = True)
    finding_date = db.Column(db.Date, nullable = True)
    description = db.Column(db.Text, nullable = True)
    source_month = db.Column(db.Date, nullable = True, index = True)

    def __repr__(self):
        return f"<DasNps {self.sample_number}: {self.substance_name}>"