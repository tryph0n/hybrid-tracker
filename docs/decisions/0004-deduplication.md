# 0004. De-duplication across sources

Status: Proposed.
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
candidate and its rows are close. Matched pairs make clusters, and a fixed
source order picks the canonical record of each cluster. A cluster counts as
one incident.

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
- EODI gives no size for `approximate`, and no pair in the sample has two rows
  with a day, so no other width can be measured yet.

### Candidate pairs

A pair is a candidate when its two rows:

- come from different sources,
- share at least one country of `countries`,
- have the same `domain`,
- and have dates that match under the date rule.

Reason: one country in one month has incidents of different kinds, for example
a disinformation campaign and a drone flight. The domain keeps them apart.

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

Titles: the titles are similar when their token-set ratio is at least 40 out of
100. Reasons:

- On the sample, the lowest title score of a match with a similar place is 45.
  The threshold 40 keeps a margin.
- A higher threshold removes false matches and true matches. At 60, the false
  matches go from 9 to 2 and the true matches from 13 to 7 (precision 0.78,
  recall 0.27). At 70, the precision is 0.80 and the recall 0.15. At any
  threshold, the precision is at most 0.83 and the recall at most 0.50, so no
  threshold reaches the targets. Of the candidates with a similar place, the
  pair with the highest title score is a false match: `eodi:EODI-2025-00094`
  (Brønnøysund on 30 September) scores 80 against the Leiden row of
  28 September.

The trial measure below used RapidFuzz 3.14: `fuzz.token_set_ratio` for the
titles and `fuzz.ratio` for the words.

### Clusters

A cluster holds at most one row of each source. The matched pairs join clusters
in the order of their title score, highest first. On a tie, the pair with the
smaller date gap comes first, then the pair with the smaller `event_id`. A pair
joins the clusters of its two rows only when, after the join, no source has
two rows in the cluster.

Reason: one Leiden row can name several incidents that EODI records one by one
(see the facts). If all matched pairs joined, the five EODI rows of the Danish
airports would make one cluster, and the count would go down. With one row of
each source, two rows of one source never join. A false match can put a Leiden
row in the wrong cluster, but it cannot merge two EODI incidents.

### Source order

The canonical record of a cluster is its row from the first source in this
order: `eodi`, `leiden`. A new source gets its place in the ticket that adds
it.

Reason: the canonical record gives the date and the place of the incident.
EODI gives a day for each row, and coordinates for 365 of its 431 rows. Leiden
gives a month (one row: a year) and a country or a sea, and no coordinates.

### Labelled sample

`tests/fixtures/dedup_pairs.csv` holds 130 pairs from the late-2025 overlap:
EODI rows from 31 August 2025 to 1 January 2026, and Leiden rows that end on or
after 1 September 2025. The columns are `event_id_a` (the EODI row),
`event_id_b` (the Leiden row), `label` (`match` or `no_match`) and `note`. The
labels come from the content of the rows: titles, places, dates, domains and
links. A note that starts with "Unsure" marks a label that the rows do not
settle (8 pairs). The Leiden adapter makes each id from a hash of the year,
the month, and the `What` and `Where` text (`event_ids` in
`src/tracker/leiden.py`), so an upstream edit of that text changes the id with
no error: the measure in #94 must fail when a pair names an `event_id` that is
not in the data.

| Pairs | Total | `match` | `no_match` |
| --- | --- | --- | --- |
| Candidates | 83 | 17 | 66 |
| Other domain, dates match | 30 | 9 | 21 |
| Same domain, other dates | 10 | 0 | 10 |
| Other domain, other dates | 7 | 0 | 7 |
| All | 130 | 26 | 104 |

All 26 matches have dates that match under the date rule.

### Targets

On the sample, counted by pairs: precision at least 0.95 and recall at least
0.80. These are objectives. The clusters and canonical records ticket (#94)
measures them on its code.

Trial measure on the sample. The same sample set the thresholds, so the
numbers are a best case.

| Rule | Pairs that match | True matches | Precision | Recall |
| --- | --- | --- | --- | --- |
| This record | 22 | 13 | 0.59 | 0.50 |
| All candidates match | 83 | 17 | 0.20 | 0.65 |
| This record with no domain condition | 30 | 21 | 0.70 | 0.81 |

The rules of this record do not reach the targets on the sample:

- Recall: the domain condition removes 9 of the 26 matches. In these 9 pairs,
  the EODI row is in `air`, and the Leiden row has the `EventCategory`
  "Reconnaissance" (8 pairs) or "Unknown" (1 pair), which the adapter maps to
  `espionage` or `other`. So the candidates hold at most 0.65 of the matches. The place rule misses 4 more:
  the Leiden title does not name the place (Dublin, Vilnius, Skrydstrup), or
  the EODI place is not the airport of its title (`eodi:EODI-2025-00005` is at
  Charleroi, and its title names Brussels).
- Precision: 7 of the 9 false matches are another incident at the same site
  in the same month, for example Liège on 8 and 9 November 2025, and
  Brønnøysund and Bardufoss on 30 September 2025. A `month` date cannot tell
  them apart. In the 2 other false matches, the Leiden row names Brussels to
  exclude it ("BUT NOT BRUSSELS").

## Alternatives rejected

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
- A title score with no place. Titles of other incidents in one country and
  one month score as high as true matches. On the sample, at the threshold
  that keeps half of the matches (55), 12 false matches come with 13 true
  matches.

## Consequences

- The clusters and canonical records ticket (#94) applies these rules and
  measures the targets on this sample.
- The domain condition keeps the recall under the target. Review it before #94
  closes. With no domain condition, the trial recall is 0.81 and the precision
  0.70.
- The precision stays under the target while a source has `month` dates only.
  The cluster rule limits the effect: a false match moves a Leiden row to
  another cluster, and it lowers the count only when the Leiden row has no
  true match.
- No pair in the sample tests the distance rule, the `approximate` rule or the
  `year` rule. Three pairs match on the date only through the margin of one
  day, and all three are `no_match`: on the sample, the margin adds 2
  candidates and no match. Review these rules when a second source with
  coordinates or day dates arrives.
- This record does not cover duplicates in one source. EODI has some: for
  example, `eodi:EODI-2025-00005` and `eodi:EODI-2025-00335` both record a
  closure of Brussels Airport on 6 November 2025. A rule for them needs its
  own sample.
- The sample covers one period and two sources. Extend it when a source is
  added.
