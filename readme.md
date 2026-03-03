## Requirements
Requirements are listed in the requirements.txt file, and can be installed from such.

## DB Migrations
The site uses flask migrate to handle database migrations which makes things easy. Firstly, when deploying for the first time we'll need to run the init command which will firstly create the migrations folder that flask migrate needs. This looks like:

```
flask db init
```

Then, whenever a database model change is made, we can create the migration file by running:

```
flask db migrate -m "message about the migration here"
```

Then, finally, we can actually create the changes in our database by running:
```
flask db upgrade
```