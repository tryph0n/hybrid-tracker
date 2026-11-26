# Data dictionary

Schema version: {{ schema_version }}.

Every table of the two public datasets has these columns, in this order. This table is
built from `src/tracker/schema.py` at each docs build, so it follows the code.
{py:func}`tracker.schema.validate` checks the clean tables against it.

{{ columns_table }}
## Cluster columns

The two published tables have three more columns, after the ones above. The
clean tables that the adapters write do not have them: the build adds them
after it joins the rows of different sources that record one incident
(`docs/decisions/0004-deduplication.md`).

{{ cluster_columns_table }}
A cluster holds at most one row of each source. To count incidents, count the
rows where `is_canonical` is true. A cluster has one canonical record, the row
of the first source in the source order.

`cluster_id` stays the same when a source higher in the order joins the
cluster. It can differ between the open dataset and the nc dataset for one
incident, because each dataset clusters its own rows: do not join the two
datasets on `cluster_id`. Counts are de-duplicated across sources only (see
[#147](https://github.com/tryph0n/hybrid-tracker/issues/147)).
