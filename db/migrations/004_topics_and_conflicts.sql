BEGIN;

-- Three things the decision layer needs in order to be deterministic
-- and auditable rather than a matter of model opinion.
--
-- 1. What a service declares itself to be in scope for. Without this,
--    "out of scope" and "we have no knowledge on it" collapse into the
--    same answer, and the agent cannot tell a client it will not advise
--    on something from an internal knowledge gap that needs a
--    professional.
--
-- 2. Which words route an enquiry to which topic. Keeping this in the
--    database means routing is inspectable and reviewable, and a
--    routing mistake is a data fix rather than a prompt rewrite.
--
-- 3. Which knowledge units are known to contradict each other. A
--    conflict is declared by a human curator, not inferred, so
--    SOURCE_CONFLICT can never be a guess.

CREATE TABLE app.service_topics (
    service_id uuid NOT NULL
        REFERENCES app.services(id),

    topic text NOT NULL,

    -- A topic can be declared and still be out of scope, which is how
    -- the service says "we recognise this request and decline it".
    is_in_scope boolean NOT NULL DEFAULT true,

    description text,

    CONSTRAINT service_topics_pk
        PRIMARY KEY (service_id, topic),

    CONSTRAINT service_topics_topic_nonempty
        CHECK (btrim(topic) <> '')
);


CREATE TABLE app.topic_keywords (
    service_id uuid NOT NULL,

    topic text NOT NULL,

    keyword text NOT NULL,

    CONSTRAINT topic_keywords_pk
        PRIMARY KEY (service_id, topic, keyword),

    CONSTRAINT topic_keywords_topic_fk
        FOREIGN KEY (service_id, topic)
        REFERENCES app.service_topics (service_id, topic),

    CONSTRAINT topic_keywords_keyword_lowercase
        CHECK (keyword = lower(keyword) AND btrim(keyword) <> '')
);


CREATE INDEX topic_keywords_keyword_idx
    ON app.topic_keywords (keyword);


CREATE TABLE app.knowledge_unit_conflicts (
    unit_id uuid NOT NULL
        REFERENCES app.knowledge_units(id),

    conflicting_unit_id uuid NOT NULL
        REFERENCES app.knowledge_units(id),

    note text NOT NULL,

    declared_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT knowledge_unit_conflicts_pk
        PRIMARY KEY (unit_id, conflicting_unit_id),

    CONSTRAINT knowledge_unit_conflicts_distinct
        CHECK (unit_id <> conflicting_unit_id),

    CONSTRAINT knowledge_unit_conflicts_note_nonempty
        CHECK (btrim(note) <> '')
);


-- Read only for the runtime role. Scope, routing and declared
-- conflicts are curator decisions, so the agent can consult them and
-- can never author them.

GRANT SELECT ON app.service_topics TO agents_app;
GRANT SELECT ON app.topic_keywords TO agents_app;
GRANT SELECT ON app.knowledge_unit_conflicts TO agents_app;


INSERT INTO app.service_topics (service_id, topic, is_in_scope, description)
VALUES
    (
        '10000000-0000-0000-0000-000000000001',
        'tax_residency',
        true,
        'Determination of residential status for a tax year.'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'return_filing',
        true,
        'Obligation to file and the mechanics of filing.'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'double_taxation',
        true,
        'Relief where the same income is taxable in two states.'
    ),
    (
        -- Declared in scope on purpose while no knowledge unit covers
        -- it. This is what a genuine internal knowledge gap looks like,
        -- and it must produce MISSING_KNOWLEDGE, never a confident
        -- answer and never OUT_OF_SCOPE.
        '10000000-0000-0000-0000-000000000001',
        'capital_gains_on_property',
        true,
        'Gains on transfer of immovable property. No knowledge yet.'
    ),
    (
        -- Recognised and declined. The firm does not advise on this.
        '10000000-0000-0000-0000-000000000001',
        'investment_recommendation',
        false,
        'Which products to invest in. Not a service we provide.'
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'fema_residential_status',
        true,
        'Residential status under the foreign exchange law.'
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'nre_nro_accounts',
        true,
        'Eligibility and operation of non-resident accounts.'
    );


INSERT INTO app.topic_keywords (service_id, topic, keyword) VALUES
    ('10000000-0000-0000-0000-000000000001', 'tax_residency', 'residency'),
    ('10000000-0000-0000-0000-000000000001', 'tax_residency', 'resident'),
    ('10000000-0000-0000-0000-000000000001', 'tax_residency', 'residential'),
    ('10000000-0000-0000-0000-000000000001', 'tax_residency', 'rnor'),
    ('10000000-0000-0000-0000-000000000001', 'tax_residency', 'nri'),
    ('10000000-0000-0000-0000-000000000001', 'return_filing', 'return'),
    ('10000000-0000-0000-0000-000000000001', 'return_filing', 'itr'),
    ('10000000-0000-0000-0000-000000000001', 'return_filing', 'filing'),
    ('10000000-0000-0000-0000-000000000001', 'return_filing', 'file'),
    ('10000000-0000-0000-0000-000000000001', 'double_taxation', 'dtaa'),
    ('10000000-0000-0000-0000-000000000001', 'double_taxation', 'treaty'),
    ('10000000-0000-0000-0000-000000000001', 'double_taxation', 'double'),
    (
        '10000000-0000-0000-0000-000000000001',
        'capital_gains_on_property',
        'property'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'capital_gains_on_property',
        'flat'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'capital_gains_on_property',
        'capital'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'investment_recommendation',
        'invest'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'investment_recommendation',
        'portfolio'
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'fema_residential_status',
        'fema'
    ),
    ('10000000-0000-0000-0000-000000000002', 'nre_nro_accounts', 'nre'),
    ('10000000-0000-0000-0000-000000000002', 'nre_nro_accounts', 'nro'),
    ('10000000-0000-0000-0000-000000000002', 'nre_nro_accounts', 'account');

COMMIT;
