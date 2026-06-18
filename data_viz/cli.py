# Standard Library Imports
import os
import json

# External Imports
from bcrypt import hashpw, gensalt
import click

# Internal Iimports
from data_viz.database import db
from data_viz.database.models import User, Groups, UserGroups, DataSources, GroupDataSources
from data_viz.auth.auth_helpers import create_user, create_group, assign_group

# Function to create the default admin user
# This user needs to be created first, so that it can be used as the creator for whatever is defined in the seed data.
def create_admin_user():
    admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    admin_username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME")
    admin_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    admin_password = hashpw(admin_password.encode("utf-8"), gensalt()).decode("utf-8")

    if not admin_email or not admin_username or not admin_password:
        print("Admin user credentials are not fully set in the environment variables.")
        return

    existing_admin = User.query.filter_by(email=admin_email).first()
    if existing_admin:
        print(f"Admin user with email {admin_email} already exists.")
        return

    new_admin = User(
        email = admin_email,
        username = admin_username,
        site_admin = True,
        status = "active",
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
                    status = "active"
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
    def gen_visuals():
        from data_viz.generateVisuals import export_data_to_db
        from data_viz.auth.auth_helpers import reconcile_source_aliases
        print("Generating V1 visual data into the database...")
        export_data_to_db()
        # Keep group access pointing at the canonical pipeline data sources.
        merges = reconcile_source_aliases()
        if merges:
            print("Reconciled duplicate data sources:", ", ".join(merges))
        print("Visual data generation complete.")

    @app.cli.command("reconcile-sources", short_help="Merge legacy/seed-named data sources into their pipeline equivalents.")
    def reconcile_sources():
        from data_viz.auth.auth_helpers import reconcile_source_aliases
        merges = reconcile_source_aliases()
        print("Reconciled:", ", ".join(merges) if merges else "nothing to merge.")