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

class ProductionConfig():
    SECRET_KEY = os.environ["SECRET_KEY"]
    DEBUG = False
    ASSET_DEBUG = False
    SIMPLELOGIN_LOGIN_URL = os.environ.get("SIMPLELOGIN_LOGIN_URL")
    SIMPLELOGIN_HOME_URL = os.environ.get("SIMPLELOGIN_HOME_URL")
    SIMPLELOGIN_USERNAME = os.environ.get("SIMPLELOGIN_USERNAME")
    SIMPLELOGIN_PASSWORD = os.environ.get("SIMPLELOGIN_PASSWORD")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    # Add more configuration settings here as the need arises

class DevelopmentConfig(ProductionConfig):
    DEBUG = True
    ASSET_DEBUG = True
    # Add more configuration settings here as the need arises

def configure(app):
    match os.environ.get("FLASK_ENV", None):
        case "production":
            app.config.from_object(ProductionConfig)
        case "development":
            app.config.from_object(DevelopmentConfig)
        case _:
            raise("Invalid FLASK_ENV value. Check the ENV file and try again")


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code