-- Every clean table in one table, with the licence family of each row.
-- `clean` and `registry` are created by `tracker build` before this file runs.
CREATE TABLE events AS
SELECT clean.*, registry.family
FROM clean
JOIN registry USING (source);
