# Data dictionary

Schema version: {{ schema_version }}.

Every CSV file in `data/events/` has these columns, in this order. This table is
built from `src/tracker/schema.py` at each docs build, so it follows the code.
{py:func}`tracker.schema.validate` checks the stored files against it.

{{ columns_table }}
