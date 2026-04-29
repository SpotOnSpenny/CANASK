# Standard Library Imports
import os
import json

# External Imports
from bcrypt import hashpw, gensalt
import click

# Internal Iimports
from data_viz.database import db
from data_viz.database.models import User, Groups, UserGroups
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
        email=admin_email,
        username=admin_username,
        password_hash=admin_password
    )
    db.session.add(new_admin)
    db.session.commit()
    print(f"Admin user {admin_username} created successfully.")

# Pull in seed data from the json file and create the appropriate database entries for users, gorups, and gorup assignments.
def create_from_seed(admin_username = None):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed_file_path = os.path.join(project_root, "seed.json")
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
                    site_admin = user.get("site_admin", False)
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
        print("Creating database tables...")
        db.create_all()
        print("Database initialized.")
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