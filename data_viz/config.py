# Python Standard Library Dependencies
import os
from datetime import timedelta

# External Dependency Imports
from dotenv import load_dotenv

# Internal Dependency Imports


#######################################################################################
#                                        Notes:                                       #
#######################################################################################

# Configuration settings for the Flask application within the project
load_dotenv()

class Config():
    SECRET_KEY = os.environ["SECRET_KEY"]
    DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
    ASSET_DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
    SIMPLELOGIN_LOGIN_URL = os.environ.get("SIMPLELOGIN_LOGIN_URL")
    SIMPLELOGIN_HOME_URL = os.environ.get("SIMPLELOGIN_HOME_URL")
    SIMPLELOGIN_USERNAME = os.environ.get("SIMPLELOGIN_USERNAME")
    SIMPLELOGIN_PASSWORD = os.environ.get("SIMPLELOGIN_PASSWORD")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    INVITE_TOKEN_EXPIRY = timedelta(minutes=5)
    # Add more configuration settings here as the need arises

def configure(app):
    app.config.from_object(Config)

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code