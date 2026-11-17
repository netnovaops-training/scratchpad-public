"""What each data file is, and what shape it has.

THIS TABLE IS THE TWIN OF `TARGETS` IN rust/admin/src/main.rs. Two processes
write the same folder; if they disagree about which key holds the list, one of
them silently writes a file the other cannot read. Change one, change both.

The important distinction is between the two SHAPES, because it decides what
kind of editor you get:

    list      a file that is a growing table of records - places you can go,
              fixes you have written down. Edited as a grid.

    document  a file that is one set of settings - Ms Dee's hours, the panels
              on the dashboard. A grid is the wrong tool: there are no rows.
              Edited as text, with the JSON checked before it is saved.
"""

TARGETS = {
    "booking": {
        "file": "booking.json",
        "label": "Tea with Ms Dee",
        "shape": "document",
        "hint": "Location, service area, hours and rates. Read by the booking "
                "site and by the phone line when it answers.",
    },
    "mom": {
        "file": "mom.json",
        "label": "Mama",
        "shape": "document",
        "hint": "Content and settings for /mama.",
    },
    "mom-kb": {
        "file": "mom-kb.json",
        "label": "Known fixes",
        "shape": "list",
        "list_key": "articles",
        "name_key": "title",
        # Column order for the grid. Anything not named here still shows, after
        # these -- so a field added by hand in a JSON file is never hidden.
        "columns": ["title", "category", "symptoms", "answer", "steps",
                    "keywords", "note", "direct", "source"],
        "hint": "Problems already solved. A strong match is answered straight "
                "from here, with no model call and without waking the desktop.",
    },
    "legacy": {
        "file": "legacy.json",
        "label": "Legacy",
        "shape": "document",
        "hint": "Which photo folders are published. A folder not listed here "
                "has no URL at all.",
    },
    "dashboard": {
        "file": "dashboard.json",
        "label": "Dashboard",
        "shape": "document",
        "hint": "Panels, sources and layout for /dashboard.",
    },
    "jason": {
        "file": "jason.json",
        "label": "Jason",
        "shape": "document",
        "hint": "Project list and write-ups for /jason.",
    },
    "places": {
        "file": "places.json",
        "label": "Places",
        "shape": "list",
        "list_key": "places",
        "name_key": "name",
        "columns": ["name", "category", "address", "phone", "hours",
                    "good_for", "cost", "notes", "source"],
        "hint": "Somewhere-to-be listings, added to from a browser on top of "
                "the researched directory in places.yaml.",
    },
}

# Filename -> key, for working out where a dropped file belongs.
BY_FILE = {t["file"]: k for k, t in TARGETS.items()}
