## Versions

The dataset follows [semantic versioning](https://semver.org/spec/v2.0.0.html).
A removed or renamed column is a major version. Each version has a tag on the
Hugging Face dataset, `v<version>`, on the commit of its first upload. A tag is
never moved.

This dataset starts with schema 2.1.0. It has no earlier version.

### 2.6.0

One new column in `events`, the same as in the other dataset: `drone_presence`.
The values of `event_status` of the EODI rows change as described in its
"Versions" section. It is empty in the rows of the versions before this one.
The tag `v2.6.0` is on the commit of the first upload.

### 2.5.0

One new column in the `quality` table, the same as in the other dataset:
`cause`. See its "Versions" section. It is blank in the rows of the runs before
this version. The tag `v2.5.0` is on the commit of the first upload.

### 2.4.0

Two new columns in the `quality` table, the same as in the other dataset:
`downloaded_at` and `updated_by_source`. See its "Versions" section. They are
blank in the rows of the runs before this version. The tag `v2.4.0` is on the
commit of the first upload.

### 2.3.0

The same data as 2.2.0. The version follows the other dataset, which gets a new
table `threat_actors` that this dataset does not have. The tag `v2.3.0` is on
the commit of the first upload.

### 2.2.0

Three new columns, the same as in the other dataset: `cluster_id`,
`n_sources` and `is_canonical`. See its "Versions" section. `cluster_id` can
differ from the one of the other dataset for one incident. The tag `v2.2.0` is
on the commit of the first upload.

### 2.1.0

Schema 2.1.0, with the same columns as the other dataset, [Europe Hybrid
Incidents](https://huggingface.co/datasets/sam-bot/europe-hybrid-incidents).
See its "Versions" section for the history of the columns.
