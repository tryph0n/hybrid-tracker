-- An event with a place and no coordinates gets the coordinates of the
-- GeoNames town of that name in its country. `geocode` and `fold` are created
-- by `tracker build` before this file runs: one row per (folded name, country),
-- and the function that folds a name.
--
-- The place of an EODI event reads "Name, Country": the end after the last
-- comma is dropped. Only a row at `place` precision is matched, so a country,
-- a sea or a coast is never taken for a town. A row with coordinates is never
-- touched. A name with no town in the country stays empty.
UPDATE events
SET lat = geocode.lat,
    lon = geocode.lon,
    geo_precision = 'place'
FROM geocode
WHERE events.lat IS NULL
  AND events.lon IS NULL
  AND events.geo_precision = 'place'
  AND events.place LIKE '%,%'
  AND events.countries SIMILAR TO '[A-Z]{2}'
  AND geocode.country = events.countries
  AND geocode.key = fold(regexp_replace(events.place, ',[^,]*$', ''));
