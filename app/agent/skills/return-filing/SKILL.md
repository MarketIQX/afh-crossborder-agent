---
name: return-filing
description: Decide whether an Indian income tax return must be filed and what the client needs in order to file. Use when the enquiry asks about filing obligations, deadlines, ITR forms, or having missed a filing.
---

# Whether a return must be filed in India

A filing obligation does not follow from residential status alone. A
non-resident with income arising in India may still have to file, and a
resident with no Indian income may not. Establish the income position,
not just the day count.

## Facts you must have

- whether any income accrues or arises in India
- the assessment year the enquiry concerns
- whether the client holds an Indian PAN

Rental income from Indian property, interest on Indian accounts, and
capital gains on Indian assets are all India-sourced. If the client has
mentioned any of these, that fact is already established; do not ask
again.

Without an answer on India-sourced income, propose `MISSING_FACTS`.

## Order of work

1. Read the case context. Note what the client has already stated.
2. Retrieve knowledge for `return_filing` before asserting any
   obligation, deadline or form number.
3. If nothing professionally verified covers the point, propose
   `MISSING_KNOWLEDGE` and let it go to a colleague.

A deadline or a form number stated from memory is the kind of error a
client acts on. Never produce one without a retrieved, verified source.

## Writing to the client

If the client has missed a deadline, say what is needed without
speculating about penalties. Penalty exposure depends on facts you do not
have, and a guess in writing is worse than a question.

Use the prompt hint recorded against each required fact. Do not send
field names, and do not cite sections or circulars to the client.
