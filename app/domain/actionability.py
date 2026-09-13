"""What makes a run's proposal current work rather than history.

A run passes through four states and only one of them means the agent
finished and reached a conclusion:

    RUNNING    still in flight; nothing about it is settled
    SUCCEEDED  finished and proposed an action
    FAILED     broke; whatever it wrote is a partial artefact
    REFUSED    declined to proceed, and the refusal is the outcome

The runner writes the proposal through a tool call and sets the terminal
state afterwards, on an autocommit connection. So a committed revision
whose run still reads RUNNING is an ordinary intermediate state that any
concurrent reader can see, and a run that dies between those two
statements leaves one behind permanently.

`result_state <> 'FAILED'` therefore admits two kinds of proposal nobody
should be asked to act on: one still being written, and one whose run
declined to proceed. Naming the single state that qualifies is the only
form of this rule that stays correct when a fifth state is added.

Nothing here deletes or hides anything. A revision from a run that
failed, refused, or never finished stays readable on its case with its
run's outcome beside it. It is denied one thing only: being presented as
work awaiting a decision.
"""

ACTIONABLE_RUN_STATE = "SUCCEEDED"

# Every state that exists and is not the one above. Kept explicit so a
# new state cannot quietly join the actionable side by omission.
NON_ACTIONABLE_RUN_STATES = ("RUNNING", "FAILED", "REFUSED")

# Spliced into each query that picks the current revision for a case.
# One definition, because the defect this closes was three such
# selections disagreeing with each other, and the two that were fixed
# first were not the one the console renders.
CURRENT_WORK_SQL = f"a.result_state = '{ACTIONABLE_RUN_STATE}'"


def run_is_actionable(result_state):
    """Whether this run's proposal may be presented as work to do."""
    return result_state == ACTIONABLE_RUN_STATE
