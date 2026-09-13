"""Who may touch which case. One predicate, consulted everywhere.

The authorization this project needs already existed and was applied
unevenly. `approval.edit_draft`, `record_decision` and `revoke` each
refused a reviewer holding no active grant, and said so in words. But
composing a draft took no reviewer at all, and every read path -- the
case page, the request page, the queue -- asked nothing. The queue even
computed a `granted` flag per row and never filtered on it.

So the console showed every case to whoever opened it, while the buttons
underneath were correctly guarded. That is the worst arrangement of the
two: it reads as authorised because the destructive actions are, and the
private content is already on the screen.

The fix is not a new authorization model. It is one function, in one
place, that every case-scoped path calls, so enforcement stops depending
on which handler remembered. `approval._has_active_grant` now delegates
here rather than keeping a second copy of the rule.

Two conditions, both necessary:

    the reviewer is active
    the reviewer holds an unrevoked grant on this exact case

A grant is per case by design. A reviewer login is not authority over
every matter the firm holds, which is the sentence `record_decision`
already used when it refused.
"""


def may_access_case(cur, reviewer_id, case_id):
    """True only if this reviewer may see and act on this exact case.

    Returns False rather than raising for an absent reviewer, because a
    missing selection is an ordinary state of a console with no sign-in
    and not an exceptional one. Callers turn False into a refusal.

    One statement, so a caller cannot satisfy the grant half and skip
    the active half.
    """
    if not reviewer_id or not case_id:
        return False

    cur.execute(
        """
        SELECT 1
        FROM app.reviewer_case_grants g
        JOIN app.reviewers r ON r.id = g.reviewer_id
        WHERE g.reviewer_id = %s
          AND g.case_id = %s
          AND g.revoked_at IS NULL
          AND r.is_active
        """,
        (reviewer_id, case_id),
    )

    return cur.fetchone() is not None


def case_of_revision(cur, revision_id):
    """The case a proposal revision belongs to, for authorising by revision.

    The draft action arrives carrying a revision rather than a case, so
    the case has to be resolved before it can be authorised. Doing that
    here keeps the lookup next to the rule it feeds.
    """
    if not revision_id:
        return None

    cur.execute(
        """
        SELECT p.case_id::text
        FROM app.proposal_revisions r
        JOIN app.action_proposals p ON p.id = r.proposal_id
        WHERE r.id = %s
        """,
        (revision_id,),
    )
    row = cur.fetchone()

    return row[0] if row else None


def case_of_approval(cur, approval_id):
    """The case an approval belongs to, for authorising a send."""
    if not approval_id:
        return None

    cur.execute(
        """
        SELECT p.case_id::text
        FROM app.approvals a
        JOIN app.draft_messages d ON d.id = a.draft_message_id
        JOIN app.proposal_revisions r ON r.id = d.proposal_revision_id
        JOIN app.action_proposals p ON p.id = r.proposal_id
        WHERE a.id = %s
        """,
        (approval_id,),
    )
    row = cur.fetchone()

    return row[0] if row else None


def case_of_gap(cur, gap_id):
    """The case a request for help belongs to."""
    if not gap_id:
        return None

    cur.execute(
        "SELECT case_id::text FROM app.knowledge_gaps WHERE id = %s",
        (gap_id,),
    )
    row = cur.fetchone()

    return row[0] if row else None
