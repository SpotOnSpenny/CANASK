# Standard Library Imports
import os

# External Imports
from bcrypt import hashpw, gensalt

# Internal Iimports
from data_viz.database import db
from data_viz.database.models import User, Groups, UserGroups

# Function to create the default admin user
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

    @app.cli.command("drop-db", short_help="Drop the database tables")
    def drop_db():
        print("Dropping database tables...")
        db.drop_all()
        print("Database tables dropped.")