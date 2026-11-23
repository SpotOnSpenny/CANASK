ROLE_HIERARCHY = {
    "Site Admin": 4,
    "Data Owner": 3,
    "Group Admin": 2,
    "Data Viewer": 1
}
# The roles a user can hold IN A GROUP, from most access to least. Every picker and report
# lists them in this order -- use this instead of spelling the list out.
# A tuple: it is shared module state, and a picker that sorted or appended in place would
# change it for every other caller.
GROUP_ROLES = tuple(sorted((role for role in ROLE_HIERARCHY if role != "Site Admin"),
                           key = ROLE_HIERARCHY.get, reverse = True))
