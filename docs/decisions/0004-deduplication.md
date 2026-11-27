# 0004. De-duplication across sources

Status: Accepted.
Date: 2026-11-25.

## Context

The two sources overlap, and the README says that they are not de-duplicated
against each other. Several Leiden "Disruption" rows describe drone incidents
that EODI also has. More sources must not read as more incidents, so the method
is fixed and measured before a new source is counted. The clusters and
canonical records ticket (#94) applies this record.

Facts that the decision depends on (the stored tables in `data/events/` at
commit `cbad1cd`):

- EODI has 431 rows, all in the domain `air`. Each row has a day: 378 rows have
  the precision `day` and 53 `approximate`. 365 rows have coordinates
  (`geo_precision` `exact`). The 66 others have a place name or a country
  only.
- Leiden has 146 rows and no coordinates. Its place names countries or seas
  only (`geo_precision` `country` or `sea`). 145
  rows have the precision `month`, stored on the 1st of the month. One row has
  the precision `year`, stored on 1 January (`leiden:631573191ba1`, 2024). So
  a distance rule never applies to a pair with a Leiden row.
- From 2025-09-01, Leiden has 19 rows, and the titles of 12 of them name
  drones. Leiden records 5 of these 12 with the `EventCategory`
  "Reconnaissance" (4) or "Unknown" (1), and 7 with "Disruption". The Leiden
  adapter (`src/tracker/leiden.py`) maps "Reconnaissance" to the domain
  `espionage`, "Unknown" to `other` and "Disruption" to `air`.
- One Leiden row can group several incidents that EODI records one by one.
  `leiden:be7d76769768` names five Danish airports on one night.
  `leiden:2b17621a2c51` records three days at the Kleine Brogel air base "as
  one incident".
- Sources date an event at night on different days. `leiden:be7d76769768`
  says "Wednesday night 24 September". EODI dates one of its rows for these
  airports on 24 September and four on 25 September.
- Place names change with the language and the spelling: Munchen and Munich,
  Goteborg and Gothenburg, Luik and Liège, Esjberg and Esbjerg.

## Decision

We compare pairs of rows from different sources. A pair matches when it is a
candidate and its rows are close. Two curation files correct the matched pairs
that the rule gets wrong. Matched pairs make clusters, and a fixed source order
picks the canonical record of each cluster. A cluster counts as one incident.

### Date rule

Each date stands for an interval, set by its precision:

| `date_precision` | Interval |
| --- | --- |
| `day` | The day. |
| `approximate` | The day, as for `day`. |
| `month` | From the 1st to the last day of the month. |
| `year` | From 1 January to 31 December. |

When `end_date` is set, the interval ends at the end of the period of
`end_date`, with the same precision: the day, the last day of its month, or
31 December of its year.

The dates of a pair match when the two intervals overlap, or when one interval
ends on the day before the other starts.

Reasons:

- The stored date of a `month` row is the 1st of the month, not the day of the
  event. A fixed window around the stored date misses most of the month, and a
  window wide enough for the month joins incidents of other days when both
  rows have a day. The interval follows what each source records.
- The margin of one day is for events at night, which sources date on either
  day (see the facts).
- EODI gives no size for `approximate`. On the sample, an interval of up to
  3 days on each side of the date changes no pair. With 4 days, the pair
  `eodi:EODI-2025-00199` and `leiden:018349739d34` becomes a candidate, and
  with 5 days the pair `eodi:EODI-2025-00208` and `leiden:c8e2a1d1d2c5` too.
  Both are `no_match`.

### Candidate pairs

A pair is a candidate when its two rows:

- come from different sources,
- share at least one country of `countries`,
- and have dates that match under the date rule.

The domain is not a condition. Reasons:

- The sources give different domains to one incident. EODI records all its
  rows in `air`. Leiden records some drone flights with the `EventCategory`
  "Reconnaissance" or "Unknown", which the adapter maps to `espionage` or
  `other`. With a domain condition, 8 of the 22 matches of the sample are not
  candidates, and each of these incidents makes two lines in the app: one for
  the EODI row and one for the Leiden row.
- One country in one month has incidents of different kinds, for example a
  disinformation campaign and a drone flight. The match rule keeps them apart.
  On the data at commit `cbad1cd`, the candidates go from 113 to 318 with no
  domain condition, and the matched pairs go from 22 to 30. All 30 matched
  pairs are in the sample. Each of the 8 new matched pairs has an EODI row and
  a Leiden row about drones at a Belgian military base, and the Leiden row has
  the `EventCategory` "Reconnaissance" (7 pairs) or "Unknown" (1 pair). 7 of
  them are `match`, and 1 is `no_match` (a later night at the same base).

### Match rule

A candidate pair matches when one of these is true:

- Both rows have coordinates, and the great-circle distance between them is
  less than 25 km.
- The place is similar and the titles are similar, as defined below.

Text is normalised before each comparison. The text is case-folded. The letters
ø, æ, œ, ß, ł, đ, þ, ð and ı become o, ae, oe, ss, l, d, th, d and i. The text
is decomposed (Unicode NFKD) and the combining marks are removed. The words are
the runs of the letters a to z and the digits. Reason: Brønnøysund and
Bronnoysund, and Liège and Liege, must compare as equal, and NFKD does not
decompose the letters of the list.

Place: take the place of one row and remove the names of its countries. The
place is similar when each word that stays is close to a word of the title of
the other row. Two words are close when their normalised Indel similarity is
at least 75 out of 100. Test both directions. A place that holds only country
or sea names is never similar. Reasons:

- Leiden has no place below the country, but its titles name the airports and
  the bases.
- The threshold 75 accepts spelling variants and some names in another
  language: Fammene and Famenne (86), Munchen and Munich (77), Goteborg and
  Gothenburg (78). At 85, the rule loses three matches, two with Munich and
  one with Gothenburg, and one false match, Munich on 18 October.

The place rule cannot read a negation. `leiden:9a23b38abc09` says "Drones
spotted over Luik (BUT NOT BRUSSELS, see VRT NEWS) airport", and the rule
finds Brussels in this title. The splits of the curation remove these pairs.

Titles: the titles are similar when their token-set ratio is at least 40 out of
100. The numbers below count pairs (see the units in "Measures and targets").
Reasons:

- On the sample, the lowest title score of a match with a similar place is 49.
  The threshold 40 keeps a margin.
- A higher threshold removes false matches and true matches. At 60, the false
  matches go from 11 to 3 and the true matches from 19 to 13 (precision 0.81,
  recall 0.59). At 70, the precision is 0.91 and the recall 0.45. A precision
  of 0.95 or more needs a threshold of 81 or more, and then the recall is
  at most 0.18. At a threshold that gives a recall of 0.80 or more, the
  precision is 0.73 or less. A false match can have a high title score:
  `eodi:EODI-2025-00094` (Brønnøysund on 30 September) scores 80 against
  the Leiden row of 28 September, and only 4 of the 19 true matches score
  more.

The trial measure below used RapidFuzz 3.14: `fuzz.token_set_ratio` for the
titles and `fuzz.ratio` for the words. RapidFuzz is not a dependency of the
project yet. The clusters ticket (#94) adds it.

### Curation

The clusters ticket (#94) adds two files: `curation/merges.csv` and
`curation/splits.csv`. Each line names an EODI row and a Leiden row, and gives
the reason. A split removes the pair from the matched pairs. A merge adds the
pair to the matched pairs, and it joins clusters before the pairs of the rule
(see "Clusters").

| File | EODI row | Leiden row | Reason |
| --- | --- | --- | --- |
| Splits | `eodi:EODI-2025-00072` (Brussels, 4 November 2025) | `leiden:9a23b38abc09` | The Leiden title says "BUT NOT BRUSSELS". |
| Splits | `eodi:EODI-2025-00335` (Brussels, 6 November 2025) | `leiden:9a23b38abc09` | The same. |
| Merges | `eodi:EODI-2025-00077` (Dublin, 1 December 2025) | `leiden:3757f59ed926` | The drones of the visit of the Ukrainian president. The Leiden title names Ireland, not Dublin. |
| Merges | `eodi:EODI-2025-00112` (Vilnius, 4 October 2025) | `leiden:55e3134992fd` | Balloons at the airport. The Leiden title names Lithuania, not Vilnius. |
| Merges | `eodi:EODI-2025-00314` (Kiel, 25 September 2025) | `leiden:d8677c266eca` | Drones over Schleswig-Holstein. The Leiden title names the province, not Kiel. |
| Merges | `eodi:EODI-2025-00069` (Brønnøysund, 28 September 2025) | `leiden:c8e2a1d1d2c5` | The day of the Leiden row. The rule gives the Leiden row to `eodi:EODI-2025-00094` (30 September), another incident, because that pair has a higher title score. |

The files do not hold these pairs:

- `eodi:EODI-2025-00005` and `leiden:018349739d34` (`unsure`). The EODI row
  can be a copy of `eodi:EODI-2025-00335` in EODI. The ticket for duplicates
  in one source (#147) decides it.
- `eodi:EODI-2025-00309` (Skrydstrup) and `leiden:eddd659ee167` (`no_match`).
  The news puts the Skrydstrup sightings on another night. The rule joins the
  Leiden row to `eodi:EODI-2025-00015` (Karup), the incident of its night.

Reason: on the sample, the rule alone gets some pairs wrong, and no threshold
fixes them (see "Measures and targets"). The curation files keep the decision
of a person and its reason in the data, and the rule stays the same.

### Clusters

A cluster holds at most one row of each source. The merges of the curation
join clusters first. Then the matched pairs of the rule join clusters in the
order of their title score, highest first. On a tie, the pair with the smaller
date gap comes first, then the pair with the smaller `event_id`. A pair joins
the clusters of its two rows only when, after the join, no source has two rows
in the cluster.

A Leiden row can match several EODI rows, for example when it names several
sites or several nights. It joins one of these rows, in the order above. The
other EODI rows stay in their own clusters, and each one counts as one
incident. The app shows no link between these clusters.

The date gap of a pair is the number of days between its two intervals. It is
0 when the intervals overlap. So for a Leiden row with a `month` date, the gap
is 0 for each EODI row of that month, and the title score decides.

Reasons:

- One Leiden row can name several incidents that EODI records one by one (see
  the facts). If all matched pairs joined, the five EODI rows of the Danish
  airports would make one cluster, and the count would go down. With one row
  of each source, two rows of one source never join. A false match can put a
  Leiden row in the wrong cluster, but it cannot merge two EODI incidents.
- A pair of the rule can take the place of a source in a cluster before a
  merge. The rule pair of `leiden:c8e2a1d1d2c5` and `eodi:EODI-2025-00094`
  scores 80, and the merged pair with `eodi:EODI-2025-00069` scores 56. If the
  merge came after the rule, the cluster rule would refuse it. With the merges
  first, no split is necessary for the pair with `eodi:EODI-2025-00094`.
- EODI records one site on one day as one row. On the sample, 6 Leiden rows
  have more than one EODI row labelled `match` (11 rows more). The count does
  not depend on the EODI row that the Leiden row joins: the Leiden row counts
  once, and each EODI row counts once.
- A link from the Leiden row to its other EODI rows would come from the
  matched pairs of the rule. For these 6 Leiden rows, 9 of the 20 links are
  another incident, on another day or night, at a site that the Leiden row
  names (`no_match`). A link would show a relation that the sources do not
  record.

The cluster view shows the date, the place, the domain and the title of each
row of the cluster, not only those of the canonical record.
Reason: the rows of one cluster can differ. For example,
`leiden:be7d76769768` names five airports, and its canonical record
`eodi:EODI-2025-00459` names three of them.

### Cluster id

The `cluster_id` of a cluster is the `event_id` of its canonical row: the row
from the first source in the source order that the cluster holds.

Reason: an EODI row keeps its own `event_id`, which the source gives. The id is
always the id of a row in the cluster, so it never names another incident. At
worst it disappears, when the row leaves the table.

Contract: `cluster_id` groups the rows of one version of the table. To keep a
reference across versions, store an `event_id` and look up its `cluster_id` in
that version.

The `cluster_id` of an incident changes when:

- a row of a source higher in the order joins the cluster, because it becomes
  the canonical row;
- the canonical row leaves the cluster, for example when a split or a rule
  change moves it;
- the `event_id` of the canonical row changes. A Leiden `event_id` is a hash of
  the text of the row, made by the adapter, not given by the source, so an
  upstream edit of that text changes it;
- the source order changes;
- a line of the curation or a change of the rules moves rows between clusters.

The two datasets can give a different `cluster_id` to one incident. Each
dataset clusters its own rows. The `nc` dataset holds the rows of every
source, and the open dataset holds the rows of the open sources only. When the
canonical row of a cluster is from a NonCommercial source, the `nc` cluster
takes the id of that row, and the open cluster takes the id of another row. Do
not join the two datasets on `cluster_id`. Today every source is open, so the
two datasets give the same ids.

### Source order

The canonical record of a cluster is its row from the first source in this
order: `eodi`, `leiden`. A new source gets its place in the ticket that adds
it.

Reason: the canonical record gives the date and the place of the incident.
EODI gives a day for each row, and coordinates for 365 of its 431 rows. Leiden
gives a month (one row: a year) and a country or a sea, and no coordinates.

### Counts

The app shows one line for each incident (ticket #165). The line shows the
value of each source for the fields of one source, and the counts use the rows
that pass the filters. The rules:

- The source filter sets the scope. A row of a source that is not selected is in
  no line and in no count.
- The line takes its title, dates, place and coordinates from the first row in
  scope, in the source order. When that source is out of scope, the next one
  gives them.
- The line shows the domain, category, status, attribution level, actors,
  attributed by and countries of every row in scope. One value shows when
  the rows agree. Otherwise each value shows with the name of its source:
  "Air (EODI); Espionage (Leiden)". When the rows disagree, an empty value
  shows as an em dash and its source: "— (EODI); Russian Federation
  (Leiden)". When every row is empty, the cell stays empty. The source links
  are one column for each selected source, in the source order, with the
  link of the row of that source: the cell is empty when the source has no
  row for the incident. A filter does not change a line. Only the source
  filter does.
- A filter on a field of one source keeps an incident when one row in scope
  passes all these filters together. The period filter tests the date of the
  line. A row of the incident that passes decides: an incident whose EODI row
  is `air` and `dismissed`, and whose Leiden row is `espionage` and
  `confirmed`, does not pass "domain `air`, status `confirmed`".
- An incident counts once in each value that a passing row gives it, so the
  counts by domain can add up to more than the number of incidents. The
  Incidents and Officially attributed figures count an incident once. The
  official count by state takes its states from the passing rows that are
  `official`, on the same row.
- The "Last 30 days" figure and its change use the date of the line, and
  ignore the period filter. The timeline counts an incident in the week or
  month of the date of the line.
- The map shows an incident with coordinates on its line, with one marker for
  each domain of the passing rows, at the same place.
- The CSV holds the rows in scope of the listed incidents, with the published
  columns.

Reasons:

- Each source states its own domain, and the sources disagree on the domain of
  one incident: a drone flight is `air` for EODI and `espionage` for Leiden.
  Picking the domain of the canonical row hides the Leiden view. The app shows
  what each source records and picks no value.
- A cluster stays in the default view when one of its rows is not
  `dismissed`. Before schema 2.6.0, EODI marked `eodi:EODI-2025-00112` (Vilnius)
  `dismissed`: EODI published its drone presence as `descartada`, and the
  tracker mapped that to `dismissed`. Leiden records the same incident
  (`leiden:55e3134992fd`): the line showed both statuses, and a status filter did
  not change the line. EODI now gives that row `confirmed`, from its `estado`.

Numbers on the data at commit `cbad1cd`, with the curation: the 577 rows make
566 clusters. The default view shows 560 incidents. The timeline counts 17
incidents in `espionage` and 9 in `other` (14 and 8 when only the canonical row
gave the domain). The counts by domain add up to more than 560.

### Labelled sample

`tests/fixtures/dedup_pairs.csv` holds 130 pairs from the late-2025 overlap:
EODI rows from 31 August 2025 to 1 January 2026, and Leiden rows that end on or
after 1 September 2025. The columns are `event_id_a` (the EODI row),
`event_id_b` (the Leiden row), `label` (`match`, `no_match` or `unsure`) and
`note`.

A pair is a `match` when its two rows record the same site on the same night
that the source of the Leiden row covers. The next night of a wave at the same
site is another incident. The labels come from the content of the rows:
titles, places, dates, domains and links. When the rows do not tell the night
or the site, news reports decide the label, and the note names the report and
gives its link (4 pairs).

A pair is `unsure` when the rows and the news do not settle it. Its note starts
with "Unsure", and the measures leave it out (1 pair). On a `match` or
`no_match` pair, a note that starts with "Unsure" marks a label that the rows
do not settle (5 pairs).

The Leiden adapter makes each id from a hash of the year, the month, and the
`What` and `Where` text (`event_ids` in `src/tracker/leiden.py`), so an
upstream edit of that text changes the id with no error: the measure in #94
must fail when a pair of the sample or a line of the curation files names an
`event_id` that is not in the data.

| Pairs | Total | `match` | `no_match` | `unsure` |
| --- | --- | --- | --- | --- |
| Candidates, same domain | 83 | 14 | 68 | 1 |
| Candidates, other domain | 30 | 8 | 22 | 0 |
| Dates that do not match | 17 | 0 | 17 | 0 |
| All | 130 | 22 | 107 | 1 |

All 22 matches have dates that match under the date rule.

### Measures and targets

The measures use two units:

- By pairs, before clusters. Each labelled pair is one unit, and the pairs
  labelled `unsure` are out. A pair is matched when the match rule accepts it.
  The precision is the number of matched pairs labelled `match`, divided by
  the number of matched pairs. The recall is the number of matched pairs
  labelled `match`, divided by the number of pairs labelled `match`.
- By Leiden rows, after clusters. Each Leiden row of the sample is one unit
  (23 rows). A Leiden row is joined when its cluster holds an EODI row, and
  the join is right when the sample labels this pair `match`. The precision is
  the number of right joins, divided by the number of joined Leiden rows. The
  recall is the number of right joins, divided by the number of Leiden rows
  that have at least one pair labelled `match` (11 rows). This unit gives the
  count of incidents.

Targets: after the curation, by Leiden rows, precision at least 0.95 and
recall at least 0.80. The clusters and canonical records ticket (#94) measures
them on its code.

Floor: the test of #94 prints the precision and the recall of the rule alone,
by pairs, and fails when one of them is less than the floor: precision 0.63
(19 of 30) and recall 0.86 (19 of 22). Reason: the targets apply after the
curation, so a change that makes the rule worse can pass the targets. The
floor finds it. When the labels change, measure the floor again and change it
in this record.

Trial measure on the sample, on the data at commit `cbad1cd`. The same sample
set the thresholds and the curation, so the numbers are a best case.

| Rule | Unit | Matched | Right | Precision | Recall |
| --- | --- | --- | --- | --- | --- |
| This record: rule alone | Pairs | 30 | 19 | 0.63 | 0.86 |
| This record: rule and curation | Leiden rows | 11 | 11 | 1.00 | 1.00 |
| Rule alone | Leiden rows | 9 | 7 | 0.78 | 0.64 |
| Rule with a domain condition | Pairs | 22 | 12 | 0.55 | 0.55 |
| All candidates match | Pairs | 112 | 22 | 0.20 | 1.00 |

The rule alone does not reach the targets:

- Recall: the place rule misses 3 matches, because the Leiden title does not
  name the EODI place: Dublin, Vilnius and Kiel. The merges add them.
- Precision: 9 of the 11 false matches are another incident at a site that the
  Leiden row names, in the same month. Examples: Liège on 8 and 9 November
  2025, Brønnøysund and Bardufoss on 30 September 2025, and Aalborg and
  Kleine Brogel on the next night of a wave. A `month` date cannot tell them
  apart. In the 2 other false matches, the Leiden row names Brussels to
  exclude it. The splits remove these 2. After the merges, each Leiden row
  with one of the 9 false matches joins an EODI row that the sample labels
  `match`, so these false matches do not change the count.

## Alternatives rejected

- Count a cluster only in the domain of its canonical record. The counts by
  domain add up to the number of incidents, but the app hides the domain that
  the other source gives. On the data at commit `cbad1cd`, `espionage` shows 14
  incidents where the sources record 17, and `other` shows 8 where they record
  9. The app counts an incident in each domain of its rows instead (see
  "Counts").
- No date rule. Two incidents at one site on different dates would make one
  cluster, and the count would go down.
- A fixed window of days around the stored date. The stored date of a Leiden
  row is the 1st of the month. A short window misses most real pairs, and a
  wide window joins incidents of other days when both rows have a day.
- Read the day in the Leiden title, for example "24 September" or "1-3
  November". This removes most false matches at one site, but it parses free
  text from one source, and the day in the text can differ from the other
  source: `leiden:3757f59ed926` gives 4 December 2025 for the drones of the
  visit that `eodi:EODI-2025-00077` dates on 1 December.
- Join all matched pairs into clusters. A Leiden row that names five airports
  would merge five EODI incidents into one.
- Join one EODI row, and link the Leiden row to its other matched EODI rows.
  On the sample, 9 of the 20 links are another incident at the same site, and
  no measure checks the links.
- A Leiden row with several EODI matches joins none. The 6 incidents of the
  sample count twice, and the recall by Leiden rows goes down to 0.45.
- A title score with no place. Titles of other incidents in one country and
  one month score as high as true matches. On the sample, a title score of at
  least 52 keeps 21 of the 22 matches, with 25 false matches. The place and
  title rule keeps 19 matches, with 11 false matches.
- A domain condition on the candidates. 8 of the 22 matches are not
  candidates, the recall by pairs goes down to 0.55, and an incident that the
  two sources record in different domains makes two lines in the app.

## Consequences

- The clusters and canonical records ticket (#94) applies these rules, adds
  the curation files, and measures this sample with both units.
- The rule alone stays under the targets while a source has `month` dates
  only. The curation files fix the pairs of the sample only, so the rule can
  get a new row wrong with no failed test.
- No pair in the sample tests two rows of different kinds, for example a
  disinformation campaign and a drone flight, that the match rule accepts.
  With no domain condition, such a pair makes one cluster. On the data at
  commit `cbad1cd`, no such pair matches (see "Candidate pairs"). Look at the
  matched pairs of different domains when a source is added.
- No pair in the sample tests the distance rule or the `year` rule. 14 pairs
  have an EODI row with an `approximate` date, and 2 of them are matches. An
  interval wider than the day changes one of these pairs only from 4 days on
  each side of the date (see the date rule). Three pairs match on the date
  only through the margin of one day, and all three are `no_match`: on the
  sample, the margin adds 3 candidates and no match. Review these rules when a
  second source with coordinates or day dates arrives.
- 36 EODI rows have no coordinates and a place of country or sea names only
  (30 name a country, 6 a sea and a country). The match rule can never join
  them to a Leiden row: there is no distance, and no place word. Only a merge
  can join them.
- This record does not cover duplicates in one source (#147). EODI has some:
  for example, `eodi:EODI-2025-00005` and `eodi:EODI-2025-00335` both record
  a closure of Brussels Airport on 6 November 2025. Until #147 is done, the
  counts are de-duplicated across sources only. The README text that #94
  writes says "de-duplicated across sources only" and links to #147.
- The sample covers one period and two sources. Extend it when a source is
  added.
