# No revision

The plugin's migrations directory when the trial is **inactive** (`CHOREGOS_ONTOLOGY_TRIAL` unset):
`python -m choregos_api.migrate` reads an empty branch here, and the database has none of the plugin's
tables. Active, the entry point names `../versions`.
