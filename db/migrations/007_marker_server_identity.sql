BEGIN;

-- The disposable-target mark compared a typed database name against the
-- connected database name. Two servers can carry the same database
-- name, so that comparison could be satisfied on the wrong server.
--
-- The mark now records the PostgreSQL cluster's system identifier,
-- which is generated at initdb and differs between clusters. The guard
-- requires the live cluster to present the same identifier, so a mark
-- cannot authorise writes on a different server, and a mark carried
-- into another cluster by a restore does not travel with its authority.
--
-- Nullable because migration 006 may already have been applied. The
-- guard treats a mark without an identifier as invalid, so an older
-- mark must be replaced deliberately rather than silently trusted.

ALTER TABLE app.disposable_test_target
    ADD COLUMN system_identifier text;

COMMIT;
