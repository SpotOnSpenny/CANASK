"""Plain factory helpers. Each adds + flushes (never commits - the db_session fixture's
savepoint pattern owns transaction lifecycle) and returns the model instance."""
import itertools
from datetime import datetime, timedelta, timezone

from bcrypt import gensalt, hashpw
from flask import current_app

from data_viz.database import db
from data_viz.database.models import (
    DataPoints,
    DataSources,
    DasDrugCodes,
    DasNps,
    DasQuant,
    DasSampleDrugs,
    DasSamples,
    GroupDataSources,
    Groups,
    GroupVisuals,
    InviteGroups,
    Invites,
    PasswordResets,
    SiteAdminKey,
    User,
    UserGroups,
    Visuals,
)

_counter = itertools.count(1)

# Factory emails use example.org, NOT example.test: the app's validate_email
# (email_validator) rejects special-use TLDs like .test, so a .test address would 400
# any route that re-validates the email.


def unique(prefix="x"):
    """Collision-free suffix for names/emails within a test run."""
    return f"{prefix}{next(_counter)}"


# One shared bcrypt hash: hashing per-user at the default cost (~100ms) would dominate
# the suite's runtime. checkpw reads the cost factor from the hash itself, so the login
# flow verifies low-cost hashes fine. Satisfies validate_password (>=12 chars, upper,
# lower, digit, special) so factory users can also log in through the real form.
TEST_PASSWORD = "Sufficiently-strong-pw1!"
_PASSWORD_HASH = hashpw(TEST_PASSWORD.encode("utf-8"), gensalt(rounds=4)).decode("utf-8")

# Same trick for the shared site admin key: dozens of gated-flow tests need one
# set, and set_site_admin_key hashes at full cost. Its own lifecycle tests still call the
# real helper; everything else seeds this row directly.
SITE_ADMIN_KEY_SECRET = "Removal-secret-value-1!"
_SITE_ADMIN_KEY_HASH = hashpw(SITE_ADMIN_KEY_SECRET.encode("utf-8"), gensalt(rounds=4)).decode("utf-8")


def seed_site_admin_key():
    """Insert/refresh the single site_admin_key row with a cheap hash of SITE_ADMIN_KEY_SECRET."""
    row = db.session.get(SiteAdminKey, 1) or SiteAdminKey(id=1)
    row.password_hash = _SITE_ADMIN_KEY_HASH
    db.session.add(row)
    db.session.flush()
    return row


def make_user(email=None, username=None, status=User.STATUS_ACTIVE, site_admin=False,
              group=None, role="Data Viewer"):
    name = username or unique("user")
    user = User(
        email=email or f"{name}@example.org",
        username=name,
        password_hash=_PASSWORD_HASH,
        status=status,
        site_admin=site_admin,
    )
    db.session.add(user)
    db.session.flush()
    if group is not None:
        add_membership(user, group, role)
    return user


def make_group(name=None, created_by=None, description=None):
    creator = created_by or make_user()
    group = Groups(name=name or unique("group"), description=description,
                   created_by=creator.id)
    db.session.add(group)
    db.session.flush()
    return group


def add_membership(user, group, role="Data Viewer"):
    membership = UserGroups(user_id=user.id, group_id=group.id, role=role)
    db.session.add(membership)
    db.session.flush()
    return membership


def make_data_source(name=None, link=None, about=None):
    source = DataSources(name=name or unique("source"), link=link, about=about)
    db.session.add(source)
    db.session.flush()
    return source


def make_visual(province="ontario", name=None, visibility="public", data_source=None,
                **overrides):
    """A minimal valid Visuals row; pass model columns (metric, data_shape, chart_type,
    level, menu_parent, is_default, ...) as overrides."""
    fields = {
        "name": name or unique("visual"),
        "province": province,
        "vis_type": overrides.pop("vis_type", "flat_series"),
        "visibility": visibility,
        "data_source_id": data_source.id if data_source is not None else None,
    }
    fields.update(overrides)
    visual = Visuals(**fields)
    db.session.add(visual)
    db.session.flush()
    return visual


def grant_visual(group, visual):
    grant = GroupVisuals(group_id=group.id, visual_id=visual.id)
    db.session.add(grant)
    db.session.flush()
    return grant


def grant_data_source(group, source):
    grant = GroupDataSources(group_id=group.id, data_source_id=source.id)
    db.session.add(grant)
    db.session.flush()
    return grant


def make_datapoint(source, geo="ontario", time_frame="2024", data_metric="deaths",
                   data_type="counts", data_value=1.0, geo_type="province",
                   time_frame_type="yearly", **overrides):
    point = DataPoints(
        data_source_id=source.id,
        geo_type=geo_type,
        geo=geo,
        time_frame_type=time_frame_type,
        time_frame=time_frame,
        data_metric=data_metric,
        data_type=data_type,
        data_value=data_value,
        **overrides,
    )
    db.session.add(point)
    db.session.flush()
    return point


def make_invite(email=None, sent_by=None, groups=(), site_admin_invite=False,
                status="pending", expires_in=timedelta(hours=72),
                expiry_task_id="task-1"):
    """groups: iterable of (group, role) pairs."""
    sender = sent_by or make_user(site_admin=True)
    invite = Invites(
        email=email or f"{unique('invitee')}@example.org",
        status=status,
        expires_at=datetime.now(timezone.utc) + expires_in,
        expiry_task_id=expiry_task_id,
        sent_by=sender.id,
        site_admin_invite=site_admin_invite,
    )
    db.session.add(invite)
    db.session.flush()
    for group, role in groups:
        db.session.add(InviteGroups(invite_id=invite.id, group_id=group.id, role=role))
    db.session.flush()
    return invite


def make_password_reset(user, expires_delta=timedelta(hours=1), used=False):
    reset = PasswordResets(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + expires_delta,
        used_at=db.func.current_timestamp() if used else None,
    )
    db.session.add(reset)
    db.session.flush()
    reset.generate_jwt(current_app.config["PASSWORD_RESET_JWT_SECRET"])
    db.session.flush()
    return reset


# --- DAS row-level tables ------------------------------------------------------------------

def make_das_drug(code=None, display_name=None, english_name=None):
    drug = DasDrugCodes(
        code=code or unique("DRUG-"),
        display_name=display_name or (code or "Drug"),
        english_name=english_name or display_name or code,
    )
    db.session.add(drug)
    db.session.flush()
    return drug


def make_das_sample(sample_number=None, province="ON", city="Toronto", drugs=(),
                    **overrides):
    """drugs: iterable of DasDrugCodes rows; builds the das_sample_drugs join rows and
    the denormalized drugs_identified list the way ingest does."""
    drugs = list(drugs)
    fields = {
        "sample_number": sample_number or unique("S-"),
        "province": province,
        "city": city,
        "drugs_identified": "; ".join(d.display_name for d in drugs) or None,
    }
    fields.update(overrides)
    sample = DasSamples(**fields)
    db.session.add(sample)
    db.session.flush()
    for position, drug in enumerate(drugs, start=1):
        db.session.add(DasSampleDrugs(sample_number=sample.sample_number,
                                      drug_code=drug.code, position=position))
    db.session.flush()
    return sample


def make_das_quant(sample_number=None, drug=None, quantity=1.0, units="mg",
                   province="ON", **overrides):
    row = DasQuant(
        sample_number=sample_number or unique("S-"),
        drug_code=drug.code if drug is not None else None,
        quantity=quantity,
        units=units,
        province=province,
        **overrides,
    )
    db.session.add(row)
    db.session.flush()
    return row


def make_das_nps(sample_number="N/A*", drug=None, substance_name="Novel substance",
                 province="ON", **overrides):
    row = DasNps(
        sample_number=sample_number,
        drug_code=drug.code if drug is not None else None,
        substance_name=substance_name,
        province=province,
        **overrides,
    )
    db.session.add(row)
    db.session.flush()
    return row
