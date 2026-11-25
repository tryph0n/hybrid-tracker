---
license: cc-by-4.0
pretty_name: Europe Hybrid Incidents
language:
  - en
---

# Europe Hybrid Incidents

Drone incidents, sabotage and other hybrid operations in Europe since 2022,
compiled from open datasets into one event table. One CSV file per source, all
with the same columns.

This dataset is a mirror, updated by the pipeline of the project after each
refresh of the stored data.

- Code and pipeline: [github.com/tryph0n/hybrid-tracker](https://github.com/tryph0n/hybrid-tracker)
- Live dashboard: [hybrid-tracker.onrender.com](https://hybrid-tracker.onrender.com)
- Columns and values: [data dictionary](https://hybrid-tracker.readthedocs.io/en/latest/data-dictionary.html)

## Sources and licence

Modified: unified schema. The events of each source are mapped to one common
set of columns. The data is distributed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Reuse must credit the
original sources:

- Lucas Alaniz Pintos, European Observatory of Drone Incidents (EODI): drone incidents in Europe. CC BY 4.0. <https://droneobservatory.eu>
- Bart Schuurman, Russian Operations Against Europe Dataset, 2022-2025, Harvard Dataverse, V4. CC BY 4.0. <https://doi.org/10.7910/DVN/TQ0FMQ>

## Versions

The dataset follows [semantic versioning](https://semver.org/spec/v2.0.0.html).
A removed or renamed column is a major version. Each version has a tag on the
Hugging Face dataset, `v<version>`, on the commit of its first upload. A tag is
never moved.

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
