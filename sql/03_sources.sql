-- The sources table of each family: the sources whose rows the family holds.
CREATE TABLE sources_open AS
SELECT * EXCLUDE (family) FROM registry WHERE family = 'open'
ORDER BY source;

CREATE TABLE sources_nc AS
SELECT * EXCLUDE (family) FROM registry
ORDER BY source;
