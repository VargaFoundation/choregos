# No revision

The plugin's migrations directory when the trial is **inactive** (`CHOREGOS_ESSAI_ONTOLOGIE` unset):
`python -m choregos_api.migrer` reads an empty branch here, and the database has none of the plugin's
tables. Active, the entry point names `../versions`.
