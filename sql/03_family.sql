-- The open family holds the rows of open sources only. The nc family holds
-- the rows of both families. The order is total, so the output is stable.
CREATE TABLE events_open AS
SELECT * EXCLUDE (family) FROM events WHERE family = 'open'
ORDER BY source, event_id;

CREATE TABLE events_nc AS
SELECT * EXCLUDE (family) FROM events
ORDER BY source, event_id;
