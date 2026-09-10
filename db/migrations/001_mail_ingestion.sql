BEGIN;

CREATE TABLE app.mailboxes (
    id uuid PRIMARY KEY,

    provider text NOT NULL,
    address text NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT mailboxes_provider_nonempty
        CHECK (btrim(provider) <> ''),

    CONSTRAINT mailboxes_address_nonempty
        CHECK (btrim(address) <> ''),

    CONSTRAINT mailboxes_provider_address_unique
        UNIQUE (provider, address)
);


CREATE TABLE app.cases (
    id uuid PRIMARY KEY,

    lifecycle_status text NOT NULL DEFAULT 'OPEN',

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT cases_lifecycle_status_check
        CHECK (
            lifecycle_status IN (
                'OPEN',
                'CLOSED'
            )
        )
);


CREATE TABLE app.inbound_messages (
    id uuid PRIMARY KEY,

    mailbox_id uuid NOT NULL
        REFERENCES app.mailboxes(id),

    provider_message_id text NOT NULL,
    provider_thread_id text,

    rfc_message_id text,
    in_reply_to text,
    references_header text,

    sender_address text NOT NULL,

    recipient_addresses jsonb NOT NULL,

    subject text,
    body_text text,

    received_at timestamptz NOT NULL,

    case_id uuid
        REFERENCES app.cases(id),

    correlation_status text NOT NULL
        DEFAULT 'UNASSIGNED',

    correlation_method text,

    created_at timestamptz NOT NULL
        DEFAULT now(),

    CONSTRAINT inbound_messages_provider_message_nonempty
        CHECK (
            btrim(provider_message_id) <> ''
        ),

    CONSTRAINT inbound_messages_sender_nonempty
        CHECK (
            btrim(sender_address) <> ''
        ),

    CONSTRAINT inbound_messages_recipients_array
        CHECK (
            CASE
                WHEN jsonb_typeof(recipient_addresses) = 'array'
                THEN jsonb_array_length(recipient_addresses) > 0
                ELSE false
            END
        ),

    CONSTRAINT inbound_messages_provider_dedup
        UNIQUE (
            mailbox_id,
            provider_message_id
        ),

    CONSTRAINT inbound_messages_correlation_status_check
        CHECK (
            correlation_status IN (
                'UNASSIGNED',
                'MATCHED',
                'AMBIGUOUS'
            )
        ),

    CONSTRAINT inbound_messages_correlation_method_check
        CHECK (
            correlation_method IS NULL
            OR correlation_method IN (
                'NEW_CASE',
                'THREAD_RFC',
                'CASE_REFERENCE',
                'MANUAL'
            )
        ),

    CONSTRAINT inbound_messages_case_correlation_consistency
        CHECK (
            (
                correlation_status = 'MATCHED'
                AND case_id IS NOT NULL
                AND correlation_method IS NOT NULL
            )
            OR
            (
                correlation_status IN (
                    'UNASSIGNED',
                    'AMBIGUOUS'
                )
                AND case_id IS NULL
                AND correlation_method IS NULL
            )
        )
);


CREATE TABLE app.ingestion_cursors (
    mailbox_id uuid PRIMARY KEY
        REFERENCES app.mailboxes(id),

    provider_cursor text,

    updated_at timestamptz NOT NULL
        DEFAULT now()
);


CREATE INDEX inbound_messages_thread_idx
    ON app.inbound_messages (
        mailbox_id,
        provider_thread_id
    )
    WHERE provider_thread_id IS NOT NULL;


CREATE INDEX inbound_messages_case_idx
    ON app.inbound_messages (
        case_id
    )
    WHERE case_id IS NOT NULL;


GRANT SELECT
    ON app.mailboxes
    TO agents_app;


GRANT SELECT
    ON app.cases
    TO agents_app;

GRANT INSERT (
    id
)
    ON app.cases
    TO agents_app;

GRANT UPDATE (
    lifecycle_status,
    updated_at
)
    ON app.cases
    TO agents_app;


GRANT SELECT
    ON app.inbound_messages
    TO agents_app;

GRANT INSERT (
    id,
    mailbox_id,
    provider_message_id,
    provider_thread_id,
    rfc_message_id,
    in_reply_to,
    references_header,
    sender_address,
    recipient_addresses,
    subject,
    body_text,
    received_at
)
    ON app.inbound_messages
    TO agents_app;

GRANT UPDATE (
    case_id,
    correlation_status,
    correlation_method
)
    ON app.inbound_messages
    TO agents_app;


GRANT SELECT
    ON app.ingestion_cursors
    TO agents_app;

GRANT INSERT (
    mailbox_id,
    provider_cursor
)
    ON app.ingestion_cursors
    TO agents_app;

GRANT UPDATE (
    provider_cursor,
    updated_at
)
    ON app.ingestion_cursors
    TO agents_app;

COMMIT;



