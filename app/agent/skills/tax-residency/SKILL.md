---
name: tax-residency
description: Follow the governed workflow for an enquiry about Indian tax residency or a move to or from India.
---

# Handling a tax-residency enquiry

This Skill is operating guidance, not professional tax knowledge. The
governed knowledge tool is the only source that may support a professional
conclusion.

## Order of work

1. Read the bound case context before asking for anything. Do not ask the
   client to repeat facts already recorded on the case.
2. Identify missing material facts from the system-provided case and service
   contract. Ask for them using the recorded client-friendly prompt hints.
3. Retrieve governed knowledge for `tax_residency` before proposing any
   professional conclusion.
4. Treat only professionally verified, effective, applicable returned
   knowledge as eligible support. Never fill a gap with general knowledge or
   this Skill's text.
5. If material case information is missing, keep it unknown and propose
   `MISSING_FACTS`. If governed knowledge cannot support a conclusion, propose
   `MISSING_KNOWLEDGE`.

## Writing to the client

Use client-friendly wording, never internal field names or statutory citations.
Keep professional authority in the internal governed record.
