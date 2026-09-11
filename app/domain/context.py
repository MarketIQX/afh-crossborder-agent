"""Bounded context assembly.

The model never chooses what case it is working on and never chooses
its own scope. This module builds the whole picture from the database,
server side, and refuses to build anything at all when the case is not
in a state a professional agent should reason about.

Budget rule: when the context does not fit, only free prose is trimmed.
Authorisation, service scope, material dates, missing facts and source
locators are mandatory and are never silently dropped, because those
are exactly the parts whose absence would make a wrong answer look
reasonable. If the mandatory part alone does not fit, assembly refuses.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import psycopg

CONTEXT_BUILDER_VERSION = "context-builder-v1"

DEFAULT_TOKEN_BUDGET = 6000

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class ContextRefused(Exception):
    """The case is not in a state that may be reasoned about."""


def tokenize(text):
    if not text:
        return frozenset()

    return frozenset(
        token for token in _TOKEN_RE.findall(text.lower()) if len(token) > 1
    )


def estimate_tokens(text):
    """Deliberately crude. Only used to decide when to trim."""
    return (len(text) + 3) // 4


@dataclass(frozen=True)
class Enquiry:
    message_id: str
    sender_address: str
    subject: str | None
    body_text: str | None
    received_at: object


@dataclass(frozen=True)
class Fact:
    predicate: str
    value_text: str | None
    status: str
    origin: str


@dataclass(frozen=True)
class TopicMatch:
    topic: str
    is_in_scope: bool
    matched_keywords: tuple


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    service_id: str
    service_key: str
    service_name: str
    reference: str | None

    enquiry: Enquiry | None
    material_date: date

    facts: tuple
    confirmed_predicates: frozenset
    material_predicates: tuple
    missing_material_predicates: tuple
    fact_prompts: dict

    topic_matches: tuple
    knowledge_release_id: str | None

    context_builder_version: str = CONTEXT_BUILDER_VERSION
    estimated_tokens: int = 0
    truncations: tuple = field(default_factory=tuple)

    @property
    def detected_topics(self):
        return tuple(match.topic for match in self.topic_matches)

    @property
    def in_scope_topics(self):
        return tuple(m.topic for m in self.topic_matches if m.is_in_scope)

    @property
    def out_of_scope_topics(self):
        return tuple(
            m.topic for m in self.topic_matches if not m.is_in_scope
        )

    @property
    def is_unroutable(self):
        return not self.topic_matches

    def to_payload(self):
        """The exact structure handed to the model."""
        return {
            "case": {
                "case_id": self.case_id,
                "reference": self.reference,
                "service_key": self.service_key,
                "service_name": self.service_name,
                "material_date": self.material_date.isoformat(),
            },
            "enquiry": (
                {
                    "sender": self.enquiry.sender_address,
                    "subject": self.enquiry.subject,
                    "body": self.enquiry.body_text,
                }
                if self.enquiry
                else None
            ),
            "facts": [
                {
                    "predicate": f.predicate,
                    "value": f.value_text,
                    "status": f.status,
                    "origin": f.origin,
                }
                for f in self.facts
            ],
            "material_facts_required": list(self.material_predicates),
            "material_facts_missing": list(self.missing_material_predicates),
            "fact_prompts": self.fact_prompts,
            "topics_detected": [
                {
                    "topic": m.topic,
                    "in_scope": m.is_in_scope,
                    "matched_keywords": list(m.matched_keywords),
                }
                for m in self.topic_matches
            ],
            "knowledge_release_id": self.knowledge_release_id,
            "context_builder_version": self.context_builder_version,
            "truncations": list(self.truncations),
        }


def _load_case(cur, case_id):
    cur.execute(
        """
        SELECT
            c.id::text,
            c.lifecycle_status,
            c.service_id::text,
            c.reference,
            s.service_key,
            s.name,
            s.is_active
        FROM app.cases c
        LEFT JOIN app.services s ON s.id = c.service_id
        WHERE c.id = %s
        """,
        (case_id,),
    )
    return cur.fetchone()


def _load_enquiry(cur, case_id):
    cur.execute(
        """
        SELECT
            id::text,
            sender_address,
            subject,
            body_text,
            received_at
        FROM app.inbound_messages
        WHERE case_id = %s
        ORDER BY received_at ASC, id ASC
        LIMIT 1
        """,
        (case_id,),
    )
    row = cur.fetchone()

    if row is None:
        return None

    return Enquiry(
        message_id=row[0],
        sender_address=row[1],
        subject=row[2],
        body_text=row[3],
        received_at=row[4],
    )


def _load_facts(cur, case_id):
    cur.execute(
        """
        SELECT predicate, value_text, status, origin
        FROM app.case_facts
        WHERE case_id = %s
        ORDER BY predicate, created_at
        """,
        (case_id,),
    )
    return tuple(
        Fact(predicate=r[0], value_text=r[1], status=r[2], origin=r[3])
        for r in cur.fetchall()
    )


def _load_required_facts(cur, service_id):
    cur.execute(
        """
        SELECT predicate, is_material, prompt_hint
        FROM app.service_required_facts
        WHERE service_id = %s
        ORDER BY predicate
        """,
        (service_id,),
    )
    rows = cur.fetchall()

    material = tuple(r[0] for r in rows if r[1])
    prompts = {r[0]: r[2] for r in rows if r[2]}

    return material, prompts


def _detect_topics(cur, service_id, tokens):
    cur.execute(
        """
        SELECT st.topic, st.is_in_scope, tk.keyword
        FROM app.service_topics st
        LEFT JOIN app.topic_keywords tk
            ON tk.service_id = st.service_id AND tk.topic = st.topic
        WHERE st.service_id = %s
        ORDER BY st.topic, tk.keyword
        """,
        (service_id,),
    )

    catalogue = {}

    for topic, in_scope, keyword in cur.fetchall():
        entry = catalogue.setdefault(topic, {"in_scope": in_scope, "hits": []})

        if keyword and keyword in tokens:
            entry["hits"].append(keyword)

    return tuple(
        TopicMatch(
            topic=topic,
            is_in_scope=entry["in_scope"],
            matched_keywords=tuple(entry["hits"]),
        )
        for topic, entry in sorted(catalogue.items())
        if entry["hits"]
    )


def _apply_budget(enquiry, budget, mandatory_tokens):
    """Trim only prose, and only after mandatory content is accounted for."""
    truncations = []

    if mandatory_tokens > budget:
        raise ContextRefused(
            f"mandatory context needs {mandatory_tokens} tokens, "
            f"budget is {budget}. Refusing to omit authorisation, scope, "
            f"material dates or missing facts to fit."
        )

    if enquiry is None or not enquiry.body_text:
        return enquiry, tuple(), mandatory_tokens

    remaining = budget - mandatory_tokens
    body_tokens = estimate_tokens(enquiry.body_text)

    if body_tokens <= remaining:
        return enquiry, tuple(), mandatory_tokens + body_tokens

    keep_chars = max(remaining * 4, 0)
    trimmed_body = enquiry.body_text[:keep_chars]

    truncations.append(
        {
            "field": "enquiry.body_text",
            "original_tokens": body_tokens,
            "kept_tokens": estimate_tokens(trimmed_body),
        }
    )

    trimmed = Enquiry(
        message_id=enquiry.message_id,
        sender_address=enquiry.sender_address,
        subject=enquiry.subject,
        body_text=trimmed_body,
        received_at=enquiry.received_at,
    )

    return trimmed, tuple(truncations), budget


def assemble(conn, case_id, token_budget=DEFAULT_TOKEN_BUDGET):
    """Build the full bounded context for one case, or refuse."""
    from app.domain import knowledge

    with conn.cursor() as cur:
        try:
            case_row = _load_case(cur, case_id)
        except psycopg.Error as exc:
            raise ContextRefused(
                f"case lookup failed: {exc.__class__.__name__}"
            ) from exc

        if case_row is None:
            raise ContextRefused(f"case {case_id} does not exist")

        (
            resolved_id,
            lifecycle_status,
            service_id,
            reference,
            service_key,
            service_name,
            service_active,
        ) = case_row

        if service_id is None:
            raise ContextRefused(
                f"case {case_id} has no service scope. A human must "
                f"triage it before an agent may reason about it."
            )

        if not service_active:
            raise ContextRefused(
                f"service {service_key} is not active for case {case_id}"
            )

        if lifecycle_status != "OPEN":
            raise ContextRefused(
                f"case {case_id} is {lifecycle_status}, not OPEN"
            )

        enquiry = _load_enquiry(cur, resolved_id)
        facts = _load_facts(cur, resolved_id)
        material, prompts = _load_required_facts(cur, service_id)

        text = " ".join(
            part
            for part in (
                enquiry.subject if enquiry else None,
                enquiry.body_text if enquiry else None,
            )
            if part
        )

        topic_matches = _detect_topics(cur, service_id, tokenize(text))
        release_id = knowledge.active_release_id(cur, service_id)

    confirmed = frozenset(f.predicate for f in facts if f.status == "CONFIRMED")
    missing = tuple(p for p in material if p not in confirmed)

    material_date = (
        enquiry.received_at.date() if enquiry else date.today()
    )

    mandatory_tokens = estimate_tokens(
        " ".join(
            [resolved_id, service_key or "", reference or ""]
            + list(material)
            + list(missing)
            + [m.topic for m in topic_matches]
            + [release_id or ""]
        )
    )

    enquiry, truncations, used = _apply_budget(
        enquiry, token_budget, mandatory_tokens
    )

    return CaseContext(
        case_id=resolved_id,
        service_id=service_id,
        service_key=service_key,
        service_name=service_name,
        reference=reference,
        enquiry=enquiry,
        material_date=material_date,
        facts=facts,
        confirmed_predicates=confirmed,
        material_predicates=material,
        missing_material_predicates=missing,
        fact_prompts=prompts,
        topic_matches=topic_matches,
        knowledge_release_id=release_id,
        estimated_tokens=used,
        truncations=truncations,
    )
