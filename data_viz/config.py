# Python Standard Library Dependencies
import os

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