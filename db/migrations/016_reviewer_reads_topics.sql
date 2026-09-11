BEGIN;

-- The reviewer could publish knowledge against a topic and could not
-- read the list of topics, so the console could not show which subjects
-- are covered and which are still empty.
--
-- Found by running the pipeline to the end rather than to the
-- interesting part: everything up to publication worked, and the first
-- read afterwards failed.
--
-- These are catalogue tables. Reading them grants no authority over any
-- case, and the reviewer already reads every table that does.

GRANT SELECT ON app.service_topics TO agents_reviewer;
GRANT SELECT ON app.topic_keywords TO agents_reviewer;
GRANT SELECT ON app.services TO agents_reviewer;

COMMIT;
