"""The size limits on inputs that every reader shares (spec 04, #214)."""

MAX_INPUT_FILE_BYTES = 512 * 1024 * 1024
"""The largest input file read whole: a query history file, a dbt artifact, an OpenLineage
event file, local or in the Inbox. A larger file is rejected before it is read."""
