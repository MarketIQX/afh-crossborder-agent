---
name: tax-residency
description: Determine Indian residential status for an individual under the day-count tests. Use when the enquiry asks whether someone is resident, non-resident or RNOR for an Indian tax year, or mentions moving to or from India.
---

# Determining Indian residential status

Residential status is decided by days physically present in India, not by
citizenship, visa, or where someone feels they live. Get the day counts
before doing anything else.

## Facts you must have

Residential status cannot be determined without both of these:

- days present in India in the year the enquiry concerns
- days present in India across the four preceding years

If either is missing, do not estimate it, do not reason around it, and do
not offer a conditional answer that covers both outcomes. Record what is
missing and propose `MISSING_FACTS`. A conditional answer reads as advice
and will be relied on as advice.

These two are also commonly needed and are usually worth asking for at
the same time, so the client is asked once rather than three times:

- the assessment year the enquiry concerns
- the current country of tax residence
- whether any income accrues or arises in India

## What you may not do

Do not state a residential status from the firm's own knowledge unless a
retrieved knowledge unit covering `tax_residency` is professionally
verified and effective. Seniority of the source is not the test; the
verification status recorded against it is.

If the only units available are source-recorded, the honest outcome is
`MISSING_KNOWLEDGE`. That is a real answer, and it goes to a colleague
rather than to the client.

## What the client already told you

Read the case context before asking for anything. Facts already recorded
against the case, even as proposals, must not be asked for again. Asking a
client to repeat what they wrote in their own email is the single fastest
way to lose their confidence in the firm.

## Writing to the client

Ask for what is missing in the client's language. Every required fact
carries a prompt hint written for a human being: use it as written.
Never send a field name. "days_present_in_india_current_year" is a column
in a database, not a question.

Do not cite statute to the client. The authority belongs in the internal
record, where it is mandatory. The client reads the answer.
