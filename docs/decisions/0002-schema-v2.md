# 0002. Schema v2

Status: Accepted.
Date: 2026-11-25.

## Context

Schema v1 links each event to Russia only (`russia_link`) and has seven
categories made for two sources (`src/tracker/schema.py`). The tracker must hold
events of several states and of new domains. The change touches every stored
file, both adapters and the dashboard, so one record fixes it before any code
changes. A change of schema major version needs a record
([record 0001](0001-record-decisions.md)).

Facts that the decision depends on:

- Most recorded incidents have no attribution: 425 of the 431 EODI events in
  `data/events/eodi.csv` today (421 of 427 when the ticket was written).
- The Leiden dataset card says: "Reuse must credit the original sources"
  (`data/events/README.md`).
- Seven Leiden rows name only a sea ("Baltic Sea" or "North Sea") and no country.
- The EODI adapter already reads two names of Ukraine (`src/tracker/eodi.py`).

## Decision

We replace schema v1 with schema v2, version 2.0.0, with the 20 columns, the
vocabularies and the rules below. The v1 values map to v2 as in the mapping
table.

### Inclusion rule

- Attribution never decides inclusion. Every event of a category in scope,
  recorded by a source of the registry (`src/tracker/sources.py`), is in the
  tracker, whatever its attribution. Reason: most recorded incidents have no
  attribution, and v2 keeps every current event.
- The interface shows the attribution level of every event. No attribution
  figure counts the `none` events. Reason: a wrong attribution shown as a fact
  can harm a person or the credibility of the project.
- Terrorism and common crime with no link to a state stay out. Reason: they are
  another subject.

### State link

- A state link is a statement by a source. The project never infers a state from
  the nationality or origin of a perpetrator. When a source records a state for
  an event, the event takes that state as the source gives it, and
  `attributed_by` says who gives it.
- Example, out: the murder of Samuel Paty, France, 2020. It is terrorism, and no
  state involvement is established
  ([source](https://en.wikipedia.org/wiki/Murder_of_Samuel_Paty)).
- Example, in: the murder of Zelimkhan Khangoshvili, Berlin, 2019. It is an
  assassination, actor `RU`, attribution `official`, after a Berlin court ruled
  in December 2021 that Russian state authorities commissioned the killing
  ([source](https://gpil.jura.uni-bonn.de/?p=4187)). The link between Chechnya
  and this project is therefore transnational repression against exiles, not
  attacks by people of Chechen origin.

### Columns

One row is one incident or one campaign. `end_date` covers campaigns. The
column order is the order of this table.

| # | Column | Type | Required | Example |
| --- | --- | --- | --- | --- |
| 1 | `event_id` | text, `<source>:<native id>`, unique | required | `eodi:EODI-2024-00003` |
| 2 | `source` | text, key of the source registry | required | `eodi` |
| 3 | `date` | date `YYYY-MM-DD`, event start | required | `2024-12-23` |
| 4 | `end_date` | date `YYYY-MM-DD`, end of a campaign | optional | `2025-02-14` |
| 5 | `date_precision` | `day`, `approximate`, `month` | required | `day` |
| 6 | `countries` | ISO 3166-1 alpha-2 codes joined by ";", empty for open sea | optional | `AT;DE` |
| 7 | `place` | text, as the source gives it | optional | `Vught, Netherlands` |
| 8 | `lat` | decimal degrees | optional | `51.65333` |
| 9 | `lon` | decimal degrees | optional | `5.2875` |
| 10 | `geo_precision` | `exact`, `place`, `country`, `sea` | required | `exact` |
| 11 | `domain` | one of the eight domains | required | `air` |
| 12 | `category` | one category of the domain | required | `drone_overflight` |
| 13 | `subcategory` | text, the source's own label, untouched | optional | `Sabotage (attempt)` |
| 14 | `title` | text, one line, English | required | `Drone overflights over Vught Penitentiary` |
| 15 | `event_status` | `confirmed`, `unconfirmed`, `dismissed` | required | `confirmed` |
| 16 | `actors` | ISO 3166-1 alpha-2 codes joined by ";" | optional | `RU` |
| 17 | `actor_name` | text, a unit or group | optional | `APT28` |
| 18 | `attribution` | `official`, `assessed`, `claimed`, `none` | required | `assessed` |
| 19 | `attributed_by` | text, who made the link | optional | `Leiden University dataset (B. Schuurman)` |
| 20 | `url` | URL of the page with details and references | required | `https://doi.org/10.7910/DVN/TQ0FMQ` |

`lat` and `lon` are both set or both empty, as in v1. The values of
`date_precision` and `event_status` do not change from v1.

### Actors and attribution

- `actors` holds the ISO codes of the linked states. The states tracked at launch
  are RU, BY, CN, IR, KP.
- `actors` is empty when `attribution` is `none`. Reason: `none` means that
  nobody attributed the event.
- `actor_name` names a unit or group, for example "APT28".
- `attribution` has four levels:
  - `official`: a government, prosecutor, court or intelligence service named the
    state.
  - `assessed`: the source's analysts or a security vendor judge the state
    responsible.
  - `claimed`: a group claimed the act and declares its allegiance.
  - `none`: no source attributes the event to a state: no state named, or
    suspicion only.
- `attributed_by` names who made the link. Reason: the interface states who
  attributed each event. It is a government office for `official`, and the
  dataset or vendor for `assessed`. For a dataset, it names the author as the
  citation of the dataset does, for example "Leiden University dataset
  (B. Schuurman)". Reason: the dataset card requires credit to the original
  sources.
- Records never name a private individual, suspects included. Reason: a wrong
  attribution can harm a person. The credited author of a published dataset is
  not a private individual named in a record.
- States outside RU, BY, CN, IR and KP: a row that names another state as the
  actor keeps that state. `actors` holds its code (for example `UA`) and
  `attribution` follows the source. Reason: nothing disappears in v2, and the
  project takes the state that the source records. Two cases are known: Ukraine
  in the drone register, and other states in the cyber incident dataset of #95
  (M3-03).

### Domains and categories

`domain` has exactly eight values. Reason: the domain carries the colour in every
chart. Each `category` belongs to one domain. No domain has more than six
categories.

| Domain | Categories |
| --- | --- |
| `air` | `drone_overflight`, `airport_disruption`, `airspace_incursion`, `airspace_disruption` |
| `sabotage` | `sabotage`, `arson` |
| `violence` | `bomb_or_threat`, `assassination` |
| `maritime` | `cable_or_pipeline_damage`, `vessel_incident` |
| `cyber` | `service_disruption`, `intrusion`, `data_theft_or_leak` |
| `information` | `influence_operation`, `symbolic_vandalism` |
| `espionage` | `reconnaissance` |
| `other` | `instrumentalised_migration`, `unclassified` |

`maritime` and `cyber` have no source yet. The categories listed for them are a
first proposal. The ticket of the first source of each domain may change them,
inside the limit of six.

### Geographic precision

`geo_precision` has four values: `exact` (coordinates), `place` (a named place,
no coordinates), `country` (countries only), and `sea` (a row that names only a
sea and no country: 7 Leiden rows today).

### European list

The European states of the scope are these 43 ISO codes: AL AD AT BE BA BG HR CY
CZ DK EE FI FR DE GR HU IS IE IT XK LV LI LT LU MT MD MC ME NL MK NO PL PT RO SM
RS SK SI ES SE CH GB VA.

- Ukraine is not in the list. Reason: an incident whose only target is Ukraine is
  out of scope, because front-line warfare inside Ukraine is another scale,
  covered by ACLED and UCDP.
- Russia and Belarus are not in the list. Reason: they are tracked actor states.
- Turkey and the Caucasus states are not in the list.

### Versioning

The dataset follows semantic versioning. A removed or renamed column is a major
version, and an added column is a minor version. Reason: outside users read
the columns by name, and an added column breaks none of those reads. Schema v2
is 2.0.0.

### Mapping from v1

The counts are the stored rows in `data/events/` at the commit of this record.

| Source | v1 value (rows) | v2 |
| --- | --- | --- |
| EODI | `drone_overflight` 206, `airport_disruption` 122, `airspace_incursion` 103 | `air` / same category name |
| EODI | `russia_link` `official` 6 | `actors` RU, `official`, `attributed_by` from the register |
| EODI | `russia_link` `none` 425 | `actors` empty, `none`. A row that the register attributes to a state outside RU, BY, CN, IR and KP keeps that state in `actors`, and `attribution` follows the source (count not stored in v1) |
| Leiden | `influence` 55: Influence operation 43, Vandalism 12 | `information` / `influence_operation`, `symbolic_vandalism` |
| Leiden | `reconnaissance` 17 | `espionage` / `reconnaissance` |
| Leiden | `sabotage_attack` 44: Sabotage 15, Sabotage (preparation) 11, Sabotage (attempt) 4, Arson 14 | `sabotage` / `sabotage` (stage kept in `subcategory`), `arson` |
| Leiden | `sabotage_attack` 9: Terrorism 6, Assassination 1, Assassination (plot) 2 | `violence` / `bomb_or_threat`, `assassination` |
| Leiden | `other` 21: Disruption 12, Disruptive immigration 5, Unknown 4 | `air` / `airspace_disruption`; `other` / `instrumentalised_migration`, `unclassified` |
| Leiden | `russia_link` `assessed` 146 | `actors` RU, `assessed` |

Check of the totals:

- EODI: 206 + 122 + 103 = 431 events by category, and 6 + 425 = 431 by
  `russia_link`.
- Leiden: 55 + 17 + 44 + 9 + 21 = 146 events by category, and 146 by
  `russia_link`.
- The v1 adapter maps one more Leiden label, "Assassination (attempt)", to
  `sabotage_attack`. No stored row has it. It maps to `violence` /
  `assassination`.

The 6 Leiden "Terrorism" rows stay in. Reason: the dataset links each of them to
Russia. Several "Disruption" rows describe the same events as EODI rows. #94
(M2-12) clusters and canonical records handles them.

## Alternatives rejected

- Keep `russia_link` and add one column for each new state. The number of
  columns grows with the number of states, and a link to several states needs
  several columns. A list of codes in `actors` does not have this problem.
- Include only events with an attribution. Most recorded incidents have none
  (425 of 431 EODI events), so this would remove almost the whole current
  dataset.
- Infer a state from the nationality or origin of a perpetrator. A wrong
  attribution shown as a fact can harm a person or the credibility of the
  project.
- Keep Ukraine in the European list. An incident whose only target is Ukraine is
  front-line warfare, a scale covered by ACLED and UCDP.

## Consequences

- Every stored file, both adapters and the dashboard change. The tickets #32
  (M1-02) to #36 (M1-06) implement the columns, vocabularies, validation and
  views of this record. This record changes no code.
- The record must be approved before #32 (M1-02) starts.
- Outside users must read the new column names. Hence the major version.
- `actors` can hold a code outside the five states tracked at launch.
- Review this record when a source adds a domain that the eight domains do not
  cover, or when a domain needs more than six categories.
