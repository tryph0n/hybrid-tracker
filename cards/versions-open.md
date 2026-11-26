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
