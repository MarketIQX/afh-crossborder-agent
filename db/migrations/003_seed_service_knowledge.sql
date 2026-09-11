BEGIN;

-- Seed the smallest versioned, approved knowledge manifest the agent
-- needs to operate, plus the material facts each service requires.
--
-- Verification honesty: every unit below is recorded as
-- SOURCE_VERIFIED, meaning a statutory locator was recorded alongside
-- the statement. None is PROFESSIONALLY_VERIFIED, because no qualified
-- professional reviewer has signed off on any of it yet. Any proposal
-- that relies on these units is therefore marked as requiring
-- professional verification. Nothing here may be presented to a client
-- as settled professional advice.

INSERT INTO app.services (id, service_key, name) VALUES
    (
        '10000000-0000-0000-0000-000000000001',
        'nri_india_tax_filing',
        'NRI India tax residency and return filing'
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'nri_fema_advisory',
        'NRI FEMA account and remittance advisory'
    );


-- Material facts. A proposal cannot reach SUPPORTED_WITHIN_POLICY
-- while any material predicate for the service is unconfirmed, which
-- is what makes "missing client facts" a database question.

INSERT INTO app.service_required_facts
    (service_id, predicate, is_material, prompt_hint)
VALUES
    (
        '10000000-0000-0000-0000-000000000001',
        'assessment_year',
        true,
        'Which Indian assessment year does the enquiry concern?'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'days_present_in_india_current_year',
        true,
        'Total days physically present in India in the relevant year.'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'days_present_in_india_preceding_four_years',
        true,
        'Total days present in India across the four preceding years.'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'india_sourced_income_present',
        true,
        'Whether any income accrues or arises in India.'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'country_of_residence',
        true,
        'Current country of tax residence.'
    ),
    (
        '10000000-0000-0000-0000-000000000001',
        'pan_available',
        false,
        'Whether the client holds an Indian PAN.'
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'account_type_in_question',
        true,
        'Which account type the enquiry concerns, for example NRE or NRO.'
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'residential_status_under_fema',
        true,
        'Residential status under FEMA, which differs from tax status.'
    );


INSERT INTO app.knowledge_releases
    (id, service_id, version, status, notes, activated_at)
VALUES
    (
        '20000000-0000-0000-0000-000000000001',
        '10000000-0000-0000-0000-000000000001',
        1,
        'ACTIVE',
        'Initial source-linked manifest. Not professionally verified.',
        now()
    ),
    (
        '20000000-0000-0000-0000-000000000002',
        '10000000-0000-0000-0000-000000000002',
        1,
        'ACTIVE',
        'Initial source-linked manifest. Not professionally verified.',
        now()
    );


INSERT INTO app.knowledge_units (
    id,
    release_id,
    unit_key,
    topic,
    statement,
    source_locator,
    verification_status,
    effective_from,
    scope_tags
) VALUES
    (
        '30000000-0000-0000-0000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        'residency_basic_test',
        'tax_residency',
        'Residential status for a tax year is determined by physical '
        'presence in India, tested against a primary day-count '
        'threshold and an alternative shorter threshold combined with '
        'presence across the four preceding years.',
        'Income-tax Act 1961, section 6(1)',
        'SOURCE_VERIFIED',
        DATE '2020-04-01',
        '["tax_residency", "day_count"]'::jsonb
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        '20000000-0000-0000-0000-000000000001',
        'deemed_residency',
        'tax_residency',
        'A separate deemed-residency provision can apply to an Indian '
        'citizen who is not liable to tax in any other country by '
        'reason of domicile or residence, subject to an India-sourced '
        'income threshold.',
        'Income-tax Act 1961, section 6(1A)',
        'SOURCE_VERIFIED',
        DATE '2020-04-01',
        '["tax_residency", "deemed_residency"]'::jsonb
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        '20000000-0000-0000-0000-000000000001',
        'rnor_status',
        'tax_residency',
        'A resident may further qualify as not ordinarily resident, '
        'which changes the scope of income brought to tax in India.',
        'Income-tax Act 1961, section 6(6)',
        'SOURCE_VERIFIED',
        DATE '2020-04-01',
        '["tax_residency", "rnor"]'::jsonb
    ),
    (
        '30000000-0000-0000-0000-000000000004',
        '20000000-0000-0000-0000-000000000001',
        'return_filing_obligation',
        'return_filing',
        'The obligation to furnish a return of income is governed by '
        'the statutory filing provision and depends on the assessee '
        'category and prescribed conditions rather than on residential '
        'status alone.',
        'Income-tax Act 1961, section 139(1)',
        'SOURCE_VERIFIED',
        DATE '2020-04-01',
        '["return_filing"]'::jsonb
    ),
    (
        '30000000-0000-0000-0000-000000000005',
        '20000000-0000-0000-0000-000000000001',
        'treaty_relief_route',
        'double_taxation',
        'Relief from double taxation for a resident of a treaty '
        'partner state is claimed under the applicable agreement, '
        'subject to the prescribed residency certification and '
        'reporting requirements.',
        'Income-tax Act 1961, sections 90 and 90A',
        'SOURCE_VERIFIED',
        DATE '2020-04-01',
        '["double_taxation", "treaty"]'::jsonb
    ),
    (
        '30000000-0000-0000-0000-000000000006',
        '20000000-0000-0000-0000-000000000002',
        'fema_status_distinct',
        'fema_residential_status',
        'Residential status under the foreign exchange law is '
        'determined separately from residential status under the '
        'income tax law, and the two can differ for the same person '
        'in the same period.',
        'Foreign Exchange Management Act 1999, section 2(v)',
        'SOURCE_VERIFIED',
        DATE '2020-04-01',
        '["fema_residential_status"]'::jsonb
    ),
    (
        '30000000-0000-0000-0000-000000000007',
        '20000000-0000-0000-0000-000000000002',
        'account_type_eligibility',
        'nre_nro_accounts',
        'Eligibility to hold and operate non-resident account types, '
        'and the permitted credits and debits for each, are governed '
        'by the deposit regulations made under the foreign exchange '
        'law.',
        'FEMA (Deposit) Regulations',
        'SOURCE_VERIFIED',
        DATE '2020-04-01',
        '["nre_nro_accounts", "remittance"]'::jsonb
    );

COMMIT;
