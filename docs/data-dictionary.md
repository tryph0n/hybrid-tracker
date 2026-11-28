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
rows where `is_canonical` is true, or the distinct `cluster_id`: the two
numbers are equal. A cluster has one canonical record, the row of the first
source in the source order. It gives the incident its id, and the app takes
the title, the dates and the place of a line from the first row of the cluster
in the source order.

The other fields can differ between the rows of one cluster: one source can
give another domain, status or attribution. The canonical record does not
decide them. The dashboard shows the value of each source on the line of the
incident, and counts an incident once in each domain that one of its rows
gives it, so the counts by domain can add up to more than the number of
incidents. To count by domain as the dashboard does, count the distinct
`cluster_id` for each `domain`, and not only the canonical rows.

`cluster_id` is the `event_id` of the canonical row. It groups the rows of one
version of the table: to keep a reference across versions, store an `event_id`
and look up its `cluster_id` in that version. It changes when a row of a source
higher in the order joins the cluster, when the canonical row leaves it, when
the `event_id` of the canonical row changes (a Leiden `event_id` is a hash of
the row text, made by the adapter), when the source order changes, and when a
curation line or a rule change moves rows. It can differ between the open dataset and the nc dataset for one
incident, because each dataset clusters its own rows: do not join the two
datasets on `cluster_id`. Counts are de-duplicated across sources only (see
[#147](https://github.com/tryph0n/hybrid-tracker/issues/147)).

## GNSS interference

The NonCommercial dataset has a context table, `gnss_interference` (added in
2.7.0), from Stanford GPS Lab (CC BY-NC 4.0). It has no event and no `event_id`:
it is not joined to `events`. It is stored as one file per month,
`gnss_interference/<YYYY>-<MM>.parquet`. One row is one H3 cell (resolution 4,
1,770 km2 on average) on one day, for the cells whose centre is in latitude 34
to 72 and longitude -25 to 45.

| Column | Meaning |
| --- | --- |
| `day` | The day of the daily file of the source (UTC). |
| `h3_index` | The H3 cell, as the source gives it. |
| `low_quality_count` | Aircraft whose ADS-B position messages report a Navigation Integrity Category below 7, a proxy for poor GNSS reception. |
| `total_aircraft_count` | All aircraft seen in the cell that day. |
| `low_quality_percent` | `low_quality_count` / `total_aircraft_count` x 100. The project computes it, the source does not give it. |

A daily count is the sum of the 24 hourly counts of the cell, so it counts
aircraft-hours, not distinct aircraft: an aircraft seen in a cell during 3 hours
counts 3 times. A cell with no aircraft has no row, so no row means no data, not
no interference. The last 7 days of the table may still change, because the
source says that recent results may be incomplete or temporarily unavailable,
and each run fetches these days again.

## Threat actors

The open dataset has a second table, `threat_actors` (added in 2.3.0): one row
per threat group, from the MISP galaxy threat-actor cluster and the curation of
the project. It has no `event_id` and no join key with `events`: look a name up
with `tracker.actors.Lookup`. The country is where the group is based; it is
not proof of state sponsorship, and the lookup never sets `attribution`.

| Column | Meaning |
| --- | --- |
| `actor` | Canonical name of the group. |
| `uuid` | UUID of the group in the MISP galaxy. Empty for a group added by the curation. |
| `aliases` | Other names of the group, as a list. |
| `country` | ISO 3166-1 alpha-2 code of the country where the group is based. Empty when no source gives it. |
| `state_sponsor` | ISO alpha-2 code of the suspected state sponsor that the MISP galaxy gives. Empty when it gives none. |
| `country_source` | Where `country` comes from: `misp-galaxy` or `curation`. |
| `country_url` | Curated countries only: the public page that says that the group is based in, or operates from, the country. |
| `country_publisher` | Curated countries only: the publisher of that page. |
| `country_quote` | Curated countries only: the exact words of that page that support the country. They give the strength of the source, for example "likely based in Russia". |
| `stance_url` | Curated countries only: a public page that calls the group pro-Russian. |
| `stance_publisher` | Curated countries only: the publisher of that page. |
| `stance_quote` | Curated countries only: the exact words of that page. |

The six columns from `country_url` to `stance_quote` are empty for a country that
comes from the MISP galaxy, and for a group with no country. The dataset card of the open dataset
lists the curated countries with these words and links.
