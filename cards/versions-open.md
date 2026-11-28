## Versions

The dataset follows [semantic versioning](https://semver.org/spec/v2.0.0.html).
A removed or renamed column is a major version. Each version has a tag on the
Hugging Face dataset, `v<version>`, on the commit of its first upload. A tag is
never moved.

The files changed with the first upload of the published tables. Until then, the
dataset held one CSV file per source (`eodi.csv`, `leiden.csv`). The tags
`v1.0.0`, `v2.0.0` and `v2.1.0` hold these files and have no `events.parquet`.
The branch `main` holds `events.parquet`, `events.csv`, `sources.parquet` and
the quality table, and no longer holds `eodi.csv` or `leiden.csv`. To read a
tag, use the file names of that tag.

### 2.7.0

The same data as 2.6.0. The version follows the NonCommercial dataset, which has
a new table (`gnss_interference`) that this dataset does not hold. The tag
`v2.7.0` is on the commit of the first upload.

### 2.6.0

One new column in `events`: `drone_presence`. It gives whether an authority
attributes the incident to a drone: `confirmed`, `not_confirmed`,
`not_substantiated` or `ruled_out`, the four words of the EODI field
`presencia_dron`. It is empty for a source that has no such field (Leiden), and
in the rows of the versions before this one. The values of `event_status` of the
EODI rows change: they now come from the EODI field `estado` (confirmed and
attributed give `confirmed`, reported gives `unconfirmed`), no longer from the
drone presence. An incident with a ruled-out drone is no longer `dismissed`. It
changes no column of `sources`, `quality` or `threat_actors`. The tag `v2.6.0`
is on the commit of the first upload.

### 2.5.0

One new column in the `quality` table: `cause`. It gives why a source needs
attention in that run: no clean table, a refresh that failed after a download,
no row in its clean table, or, for a live source, no successful fetch for more
than three days. Several causes are joined with `; `. It is blank when the
source has no cause, and in the rows of the runs before this version. A source
with no clean table is left out of the other tables, and keeps a row in
`quality` with zero rows and this cause. It changes no column of `events`,
`sources` or `threat_actors`. The tag `v2.5.0` is on the commit of the first
upload.

### 2.4.0

Two new columns in the `quality` table: `downloaded_at` and
`updated_by_source`. `downloaded_at` is the time of the newest download that
the mapping, the validation and the shrink guard accepted, so it gives the age
of the data in the dataset. `last_fetch_at` keeps its meaning: the last download
that reached the source, also when its file was then rejected.
`updated_by_source` is the date of the last change that the source gives, and it
is blank when the source gives none. Both are blank in the rows of the runs
before this version. It changes no column of `events`, `sources` or
`threat_actors`. The tag `v2.4.0` is on the commit of the first upload.

### 2.3.0

New table `threat_actors`: one row per threat group, with its aliases, its
country of origin and the suspected state sponsor when the MISP galaxy gives
one. For a curated country, the columns `country_url`, `country_publisher`,
`country_quote`, `stance_url`, `stance_publisher` and `stance_quote` give the
sources and their exact words; they are empty for a country of the MISP
galaxy. It changes no column of `events` or `sources`. The tag `v2.3.0` is on the
commit of the first upload.

### 2.2.0

Three new columns: `cluster_id`, `n_sources` and `is_canonical`. A
cluster is the rows of different sources that record one incident (record 0004
of the project). Count incidents with `is_canonical`: it is true on one row of
each cluster. The other fields can differ between the rows of a cluster: count
by domain with the distinct `cluster_id` of each `domain`, not only the
canonical rows, because one incident can have a different domain in each
source. `cluster_id` can differ from the one of the other dataset for one
incident. The older versions have no cluster columns, and each row is one
incident. The tag `v2.2.0` is on the commit of the first upload.

### 2.1.0

New value `year` for `date_precision`: a date known to the year only, stored on
1 January. No column changes. Leiden events whose month is "Unknown" in the
source (one event, `leiden:631573191ba1`) now have `year` instead of `month`;
every other Leiden row keeps `month`.

### 2.0.0

Schema v2 (record 0002 of the project). 20 columns: `domain`, `actors`,
`attribution` and `attributed_by` replace `russia_link`, and `countries`
(several codes), `end_date`, `geo_precision` and others are new.
Mapping from 1.0.0:

| Source | 1.0.0 | 2.0.0 |
| --- | --- | --- |
| EODI | `drone_overflight`, `airport_disruption`, `airspace_incursion` | `domain` `air`, same `category` |
| EODI | `russia_link` `official` | `actors` `RU`, `attribution` `official`, `attributed_by` from the register |
| EODI | `russia_link` `none` | `actors` empty, `attribution` `none` (a state that the register names keeps its code) |
| Leiden | `influence` | `information`: `influence_operation`, `symbolic_vandalism` |
| Leiden | `reconnaissance` | `espionage` / `reconnaissance` |
| Leiden | `sabotage_attack`: Sabotage, Arson | `sabotage`: `sabotage`, `arson` |
| Leiden | `sabotage_attack`: Terrorism, Assassination | `violence`: `bomb_or_threat`, `assassination` |
| Leiden | `other`: Disruption, Disruptive immigration, Unknown | `air` / `airspace_disruption`; `other` / `instrumentalised_migration`, `unclassified` |
| Leiden | `russia_link` `assessed` | `actors` `RU`, `attribution` `assessed` |

### 1.0.0

The first schema, with the column `russia_link` (`official`, `assessed`,
`none`). Tag: `v1.0.0`.

To read this version, give the tag in the path. The `storage_options`
argument `revision` is ignored, so do not use it.

```python
import pandas as pd

old = pd.read_csv("hf://datasets/sam-bot/europe-hybrid-incidents@v1.0.0/eodi.csv")
```
