# Standard Library Imports
import os
import json
import secrets

# External Imports
from bcrypt import hashpw, gensalt
from celery.result import AsyncResult
import click

# Internal Iimports
from data_viz.database import db
from data_viz.database.models import User, Groups, UserGroups, DataSources, GroupDataSources, Invites, InviteGroups
from data_viz.auth.auth_helpers import create_user, create_group, assign_group, set_site_admin_key, validate_password

# Function to create the default admin user
# This user needs to be created first, so that it can be used as the creator for whatever is defined in the seed data.
def create_admin_user():
    admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    admin_username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME")
    admin_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")

    # Guard BEFORE hashing -- a missing password would otherwise crash on .encode() with AttributeError.
    # In prod this runs in the one-shot `init` service that web/worker/beat gate on, so a fresh stack
    # with unset bootstrap creds must fail visibly (exit non-zero), not boot "successfully" with no
    # admin. But once a site admin exists the creds are allowed to be blank -- LAUNCH_TODO.md tells the
    # operator to blank BOOTSTRAP_ADMIN_PASSWORD after first launch, and init-db reruns on every prod-up.
    if not admin_email or not admin_username or not admin_password:
        if User.query.filter_by(site_admin=True).first():
            print("Bootstrap admin credentials are unset, but a site admin already exists; nothing to do.")
            return
        print("Admin user credentials are not fully set in the environment variables.")
        raise SystemExit(1)

    admin_password = hashpw(admin_password.encode("utf-8"), gensalt()).decode("utf-8")

    existing_admin = User.query.filter_by(email=admin_email).first()
    if existing_admin:
        print(f"Admin user with email {admin_email} already exists.")
        return

    new_admin = User(
        email = admin_email,
        username = admin_username,
        site_admin = True,
        status = User.STATUS_ACTIVE,
        password_hash = admin_password
    )
    db.session.add(new_admin)
    db.session.commit()
    print(f"Admin user {admin_username} created successfully.")

# Pull in seed data from the json file and create the appropriate database entries for users, gorups, and gorup assignments.
def create_from_seed(admin_username = None):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed_file_path = os.path.join(project_root, "app_config/seed.json")
    if not os.path.exists(seed_file_path):
        print("Seed file not found. Please ensure seed.json is in the project root.")
        return
    
    with open(seed_file_path, "r") as seed_file:
        seed_data = json.load(seed_file)

    # Pull the initial admin account if it exists, otherwise require a user ID to be passed in as the creator of the seeded data.
    admin_user = User.query.filter_by(username=admin_username).first() if admin_username else User.query.filter_by(username=os.environ.get("BOOTSTRAP_ADMIN_USERNAME")).first()
    if not admin_user:
        message = f"Admin user '{admin_username}' not found." if admin_username else "Admin user not found in environment variables."
        print(message)
        return

    # Create the groups first so we have goup IDs to assign the user to
    if "groups" in seed_data.keys():
        for group in seed_data["groups"]:
            existing_group = Groups.query.filter_by(name = group["name"]).first()
            if existing_group:
                print(f"Group with name {group['name']} already exists. Skipping creation.")
                continue
            create_group(
                name = group["name"],
                description = group.get("description", None),
                created_by = admin_user.id
            )
            print(f"Successfully created the group {group['name']} from seed data.")
    else:
        print("No groups found in seed data.")

    # Create the fixed data source catalog (the sources groups can be granted access to)
    if "data_sources" in seed_data.keys():
        for source in seed_data["data_sources"]:
            existing_source = DataSources.query.filter_by(name = source["name"]).first()
            if existing_source:
                print(f"Data source {source['name']} already exists. Skipping creation.")
                continue
            db.session.add(DataSources(name = source["name"], link = source.get("link")))
            print(f"Successfully created data source {source['name']} from seed data.")
        db.session.commit()
    else:
        print("No data sources found in seed data.")

    # Link groups to any data sources listed against them in the seed data
    if "groups" in seed_data.keys():
        for group in seed_data["groups"]:
            source_names = group.get("data_sources", [])
            if not source_names:
                continue
            group_obj = Groups.query.filter_by(name = group["name"]).first()
            if not group_obj:
                continue
            for source_name in source_names:
                source = DataSources.query.filter_by(name = source_name).first()
                if not source:
                    print(f"Data source {source_name} not found for group {group['name']}. Skipping link.")
                    continue
                existing_link = GroupDataSources.query.filter_by(group_id = group_obj.id, data_source_id = source.id).first()
                if existing_link:
                    continue
                db.session.add(GroupDataSources(group_id = group_obj.id, data_source_id = source.id))
                print(f"Linked group {group['name']} to data source {source_name}.")
        db.session.commit()

    # Create the users and assign them to the groups with appropriate roles
    if "users" in seed_data.keys():
        for user in seed_data["users"]:
            existing_user = User.query.filter_by(email = user["email"]).first()
            if existing_user:
                print(f"User with email {user['email']} already exists. Skipping creation for {user['username']}.")
                continue
            try:
                created_user = create_user(
                    email = user["email"],
                    username = user["username"],
                    password = user["password"],
                    invited_by = admin_user.id,
                    site_admin = user.get("site_admin", False),
                    status = User.STATUS_ACTIVE
                )
                print(f"Successfully created user {user['username']} from seed data.")
                if "groups" in user.keys():
                    for group_assignment in user["groups"]:
                        group = Groups.query.filter_by(name = group_assignment["name"]).first()
                        if group:
                            assigned_group = assign_group(
                                user_id = created_user.id,
                                group_id = group.id,
                                role = group_assignment["role"],
                                assigned_by = admin_user.id,
                            )
                            print(f"Successfully assigned user {user['username']} to group {group_assignment['name']} with role {group_assignment['role']}.")
            except Exception as e:
                print(f"Error creating user {user['username']}: {e}")

# Take all data within the database and export it to a CSV file for easy viewing so that we can easily see how things are working and connected.
def db_to_csv():
    pass

# Register commands in the flask shell
def register_cli(app):
    # register custom symbols in flask shell
    @app.shell_context_processor
    def make_shell_context():
        return {
            "db": db,
            "User": User,
            "Groups": Groups,
            "UserGroups": UserGroups,
        }
    
    @app.cli.command("init-db", short_help="Initialize the database")
    def init_db():
        print("Creating default admin user...")
        create_admin_user()
        print("Default admin user created. Database setup complete!")

    @app.cli.command("drop-db", short_help="Drop the database tables")
    def drop_db():
        print("Dropping database tables...")
        db.drop_all()
        print("Database tables dropped.")

    @app.cli.command("seed-db", short_help="Seed the database with initial user and group data from the seed.json file.")
    @click.option("--admin-username", default=None, help="The username of the admin user to be set as the creator of the seeded data.")
    def seed_db(admin_username):
        print("Seeding database with initial data from seed.json...")
        create_from_seed(admin_username = admin_username)
        print("Database seeding complete.")

    @app.cli.command("define-visuals", short_help="Sync visual definitions from app_config/visuals/*.json into the database.")
    def define_visuals():
        from data_viz.visual_definitions import sync_visual_definitions
        print("Syncing visual definitions from manifests...")
        stats = sync_visual_definitions()
        print(f"Visual definitions synced: {stats['created']} created, "
              f"{stats['updated']} updated, {stats['pruned']} pruned.")

    @app.cli.command("gen-visuals", short_help="Scrape-clean the V1 data and persist it into the database.")
    @click.option("--only", "only", multiple=True,
                  help="Regenerate only these target/province keys (e.g. --only canada). "
                       "Repeatable. Drops & rewrites just those targets' rows. Default: all.")
    def gen_visuals(only):
        from data_viz.generate_visuals import export_data_to_db
        from data_viz.auth.auth_helpers import reconcile_source_aliases
        targets = list(only) or None
        print("Generating V1 visual data into the database"
              + (f" (only: {', '.join(targets)})" if targets else "") + "...")
        export_data_to_db(only=targets)
        # Keep group access pointing at the canonical pipeline data sources.
        merges = reconcile_source_aliases()
        if merges:
            print("Reconciled duplicate data sources:", ", ".join(merges))
        print("Visual data generation complete.")

    @app.cli.command("ingest-das", short_help="Ingest the monthly Drug Analysis Service workbook from output/ into the das_* tables.")
    @click.option("--file", "file", default=None,
                  help="Specific workbook to ingest (filename in output/, or an absolute path). "
                       "Default: every <scraped>_<data-until>_nationalDAS.xlsx in output/, oldest first.")
    def ingest_das(file):
        from data_viz.das_ingest import ingest_das as run_ingest
        run_ingest(file=file)
        print("DAS ingest complete.")

    @app.cli.command("build-das-gazetteer", short_help="Rebuild the DAS city-coordinates asset from the das_* tables and a GeoNames Canada dump.")
    @click.option("--file", "file", default=None,
                  help="GeoNames dump zip (filename in output/, or an absolute path). "
                       "Default: output/geonames_CA.zip (download.geonames.org/export/dump/CA.zip).")
    def build_das_gazetteer(file):
        from data_viz.das_gazetteer import build_gazetteer
        build_gazetteer(file=file)
        print("Gazetteer build complete -- commit the refreshed das_city_coords.json.")

    @app.cli.command("clear-invites", short_help="Delete revoked invites (and their group rows) from the database, or every invite for a given email.")
    @click.option("--email", default=None, help="Delete ALL invites for this email regardless of status (default: all revoked invites).")
    def clear_invites(email):
        # Default mode deletes only revoked invites: they're already hidden from the UI and their
        # Celery expiry tasks were revoked when they were marked revoked, so a hard delete is safe.
        # With --email, every invite for that address goes regardless of status, so any still-scheduled
        # expiry task must be revoked first or it fires later against a missing row. InviteGroups rows
        # go first either way (plain FK, no cascade).
        if email:
            invites = Invites.query.filter_by(email=email).all()
            for invite in invites:
                if invite.expiry_task_id:
                    AsyncResult(invite.expiry_task_id).revoke()
        else:
            invites = Invites.query.filter_by(status="revoked").all()
        ids = [i.id for i in invites]
        if ids:
            InviteGroups.query.filter(InviteGroups.invite_id.in_(ids)).delete(synchronize_session=False)
            Invites.query.filter(Invites.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
        print(f"Cleared {len(ids)} {f'invite(s) for {email}' if email else 'revoked invite(s)'}.")

    @app.cli.command("source-access",
                     short_help="Show who can see a data source right now (or list every source and its restricted flag).")
    @click.argument("source_name", required=False)
    def source_access(source_name):
        # The check that goes with a restricted source (data sharing agreement): run it after
        # define-visuals turns the flag on, and whenever the agreement's membership changes, and
        # compare the printed people against the agreement. Read-only.
        from data_viz.auth.restricted import source_access_report
        if not source_name:
            for source in DataSources.query.order_by(DataSources.name).all():
                print(f"{'[restricted]' if source.restricted else '[open]      '} {source.name}")
            return
        source = DataSources.query.filter_by(name = source_name).first()
        if source is None:
            print(f"No data source named {source_name!r}. Run `flask source-access` to list them.")
            raise SystemExit(1)
        report = source_access_report(source)

        def describe(user):
            suffix = "" if user.status == User.STATUS_ACTIVE else f"  [{user.status}]"
            return f"{user.email} ({user.username}){suffix}"

        print(f"Data source: {source.name}")
        if report["restricted"]:
            print("Status: RESTRICTED -- only members of the groups below can see or manage it; "
                  "site admins outside them cannot.")
        else:
            print("Status: open -- every site admin can see and manage it.")

        print("\nGroups that hold this source:")
        if not report["groups"]:
            print("  (none)" + (" -- nobody can see its non-public visuals." if report["restricted"] else ""))
        for entry in report["groups"]:
            print(f"  {entry['group'].name}")
            if not entry["members"]:
                print("    (no members)")
            for member in entry["members"]:
                print(f"    {member['role']:<12} {describe(member['user'])}")
        if report["groups"]:
            print("  Data Owners see every visual; other members see the Group-level visuals "
                  "granted to their group.")

        print(f"\nSite admins who can see and manage it ({len(report['site_admins_covered'])}):")
        for admin in report["site_admins_covered"] or []:
            print(f"  {describe(admin)}")
        if report["restricted"]:
            print(f"\nSite admins who can NOT see it ({len(report['site_admins_not_covered'])}):")
            for admin in report["site_admins_not_covered"]:
                print(f"  {describe(admin)}")

        print(f"\nVisuals set to Public -- visible to everyone, signed in or not ({len(report['public_visuals'])}):")
        for visual in report["public_visuals"]:
            print(f"  {visual.province} / {visual.name}")

        if report["stray_grants"]:
            note = ("ignored while the source is restricted" if report["restricted"]
                    else "these groups CAN see the granted visuals")
            print(f"\nVisual grants on groups that do not hold this source ({note}):")
            for group, visuals in report["stray_grants"]:
                print(f"  {group.name}: {', '.join(v.name for v in visuals)}")

        print(f"\nPending invites into these groups ({len(report['pending_invites'])}):")
        for item in report["pending_invites"]:
            invite = item["invite"]
            print(f"  {invite.email} -> {item['group'].name} ({item['role']}), expires {invite.expires_at:%Y-%m-%d}")

    @app.cli.command("set-group-role",
                     short_help="Set (or with --remove, drop) a user's role in a group from the server.")
    @click.argument("email")
    @click.argument("group_name")
    @click.argument("role", required=False)
    @click.option("--remove", is_flag=True, help="Remove the user from the group instead of setting a role.")
    def set_group_role(email, group_name, role, remove):
        # Break-glass for a group that holds a restricted source: once its last Data Owner / member
        # site admin is gone, nobody can manage it in the app (site admins outside it are shut out
        # by design), so membership has to be repaired from the server. Works for any group.
        from data_viz.validation import validate_role
        user = User.query.filter_by(email = email).first()
        group = Groups.query.filter_by(name = group_name).first()
        if user is None or group is None:
            print(f"No {'user with email ' + repr(email) if user is None else 'group named ' + repr(group_name)}.")
            raise SystemExit(1)
        if remove:
            if UserGroups.query.filter_by(user_id = user.id, group_id = group.id).first() is None:
                print(f"{email} is not a member of {group_name}; nothing to remove.")
                raise SystemExit(1)
            assign_group(user.id, group.id, None, remove = True)
            print(f"Removed {email} from {group_name}.")
            return
        valid, message = validate_role(role)
        if not valid:
            print(f"Refusing to set role {role!r}: {message} Use Data Owner, Group Admin or Data Viewer.")
            raise SystemExit(1)
        existing = UserGroups.query.filter_by(user_id = user.id, group_id = group.id).first()
        if existing is not None and existing.role == role:
            print(f"{email} already has the role {role} in {group_name}.")
            return
        assign_group(user.id, group.id, role)
        print(f"{email} is now {role} in {group_name}.")

    @app.cli.command("set-group-source",
                     short_help="Give a group a data source (or with --remove, take it away) from the server.")
    @click.argument("group_name")
    @click.argument("source_name")
    @click.option("--remove", is_flag=True, help="Detach the source from the group instead of attaching it.")
    def set_group_source(group_name, source_name, remove):
        # The other half of the restricted-source break-glass: in the app only someone already
        # covered by the agreement can attach a restricted source to a group, so a restricted
        # source that NO group holds (e.g. the flag went live before its group was set up) can
        # only be given its first group from here. Other sources on the group are left alone.
        from data_viz.auth.auth_helpers import set_group_data_sources
        group = Groups.query.filter_by(name = group_name).first()
        source = DataSources.query.filter_by(name = source_name).first()
        if group is None or source is None:
            print(f"No {'group named ' + repr(group_name) if group is None else 'data source named ' + repr(source_name)}.")
            raise SystemExit(1)
        current = {gds.data_source_id for gds in GroupDataSources.query.filter_by(group_id = group.id).all()}
        wanted = current - {source.id} if remove else current | {source.id}
        changes = set_group_data_sources(group.id, wanted, changed_by = None)
        if not changes:
            print(f"{group_name} {'does not have' if remove else 'already has'} {source_name}; nothing changed.")
        else:
            print(f"{group_name} {'no longer has' if remove else 'now has'} {source_name}.")

    @app.cli.command("rotate-site-admin-key",
                     short_help="Set or rotate the shared site admin key (break-glass: no current key needed).")
    @click.option("--key", default=None,
                  help="Use this value instead of generating one. WARNING: lands in shell history; "
                       "prefer the default generate mode, which prints a strong random secret once.")
    def rotate_site_admin_key_cli(key):
        generated = key is None
        if generated:
            key = secrets.token_urlsafe(24)
        else:
            # An empty/weak explicit value would guard every site-admin membership change (an
            # empty one can never even validate -- check_site_admin_key rejects empty
            # candidates -- which would brick the gated flows until the next CLI run).
            valid, message = validate_password(key)
            if not valid:
                print(f"Refusing to set the site admin key: {message}")
                raise SystemExit(1)
        set_site_admin_key(key, changed_by = None)
        if generated:
            # Printed exactly once; it is never stored in plaintext or emailed.
            print(f"New site admin key: {key}")
            print("Share it out-of-band with the admins who should hold it. It will not be shown again.")
        else:
            print("Site admin key set from --key.")

    @app.cli.command("reconcile-sources", short_help="Merge legacy/seed-named data sources into their pipeline equivalents.")
    def reconcile_sources():
        from data_viz.auth.auth_helpers import reconcile_source_aliases
        merges = reconcile_source_aliases()
        print("Reconciled:", ", ".join(merges) if merges else "nothing to merge.")