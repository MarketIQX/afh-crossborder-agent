BEGIN;

-- Test containment.
--
-- The suites previously wrote fixtures to whatever database the
-- environment happened to select, and cleaned up by deleting rows. A
-- misconfigured environment would have written to, and deleted from, a
-- real instance. Nothing stopped it.
--
-- A database name is not a safety signal, so this does not look at
-- names. A target is writable by the test suites only if an
-- administrator has deliberately marked it, in that database, as
-- disposable. The mark is data, not schema, so this table exists
-- everywhere and is empty everywhere except on a target someone chose.
--
-- The runtime role may read the mark and may not write it: marking a
-- database disposable is an administrator's decision.

CREATE TABLE app.disposable_test_target (
    id boolean PRIMARY KEY DEFAULT true,

    dbname text NOT NULL,
    purpose text NOT NULL,
    token text NOT NULL,

    marked_at timestamptz NOT NULL DEFAULT now(),
    marked_by text NOT NULL DEFAULT current_user,
    note text,

    -- At most one mark per database.
    CONSTRAINT disposable_test_target_single_row
        CHECK (id),

    CONSTRAINT disposable_test_target_purpose_check
        CHECK (purpose = 'DISPOSABLE_TEST_TARGET'),

    CONSTRAINT disposable_test_target_dbname_nonempty
        CHECK (btrim(dbname) <> ''),

    CONSTRAINT disposable_test_target_token_nonempty
        CHECK (btrim(token) <> '')
);


GRANT SELECT ON app.disposable_test_target TO agents_app;

COMMIT;
