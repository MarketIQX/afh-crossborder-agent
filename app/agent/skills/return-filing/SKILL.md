---
name: return-filing
description: Follow the governed workflow for an enquiry about Indian return filing, deadlines, forms, or a missed filing.
---

# Handling a return-filing enquiry

This Skill is operating guidance, not professional filing knowledge. The
governed knowledge tool is the only source that may support a professional
conclusion.

## Order of work

1. Read the case context. Note what the client has already stated.
2. Identify missing material facts from the system-provided case and service
   contract; do not infer classifications or obligations from this Skill.
3. Retrieve governed `return_filing` knowledge before proposing an
   obligation, deadline, form, or other professional conclusion.
4. Use only professionally verified, effective, applicable returned knowledge
   as support. If material facts are missing, propose `MISSING_FACTS`; if the
   governed corpus cannot support a conclusion, propose `MISSING_KNOWLEDGE`.

## Writing to the client

Use the prompt hints recorded against required facts. Do not send field names,
legal citations, or an unsupported conclusion to the client.
