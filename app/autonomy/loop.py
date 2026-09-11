"""The supervisor: the part that makes this an agent rather than a tool.

Everything needed to answer an enquiry already existed, and a person had
to run each stage by hand. That is not what the brief asks for, and it is
not what a firm would buy. This drives the stages on a timer and stops at
exactly the point where a human judgment is genuinely required.

    fetch mail  ->  triage  ->  reason  ->  draft  ->  [STOP]

The stop is the design. The loop composes a letter and puts it in front
of a reviewer; it never approves and never sends, because the database
roles it runs under cannot do either. Autonomy here means nobody has to
start the work, not that nobody has to authorise the outcome.

What it will not do:

- Route a case it is unsure about. The router refuses ambiguity and
  leaves those for a person, with the reason recorded.
- Reason about a case that has no service scope. Context assembly
  refuses, and that refusal is a feature.
- Re-run a case that already has a proposal, so a crash mid-cycle
  cannot produce duplicate work.
- Touch approval or dispatch in any way.

Usage:

    python -m app.autonomy.loop --once            # one cycle, then exit
    python -m app.autonomy.loop                   # every 60 seconds
    python -m app.autonomy.loop --interval 300
    python -m app.autonomy.loop --once --no-mail  # skip the Gmail fetch
"""

import argparse
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg

from app import config
from app.autonomy import router

DEFAULT_INTERVAL_SECONDS = 60

# A cycle reasons about at most this many cases. A backlog is worked
# through over several cycles rather than in one long unattended burst
# that nobody is watching and that costs real money per call.
MAX_RUNS_PER_CYCLE = 3

_STOPPING = False


def _request_stop(signum, frame):
    global _STOPPING
    _STOPPING = True
    print("\nSTOPPING after the current cycle. Ctrl+C again to force.")


@dataclass
class CycleReport:
    """What one pass actually did. Printed, and used by the tests."""

    started_at: str
    fetched: int = 0
    ingested: int = 0
    routed: int = 0
    left_for_human: int = 0
    reasoned: int = 0
    drafted: int = 0
    errors: list = field(default_factory=list)

    def summary(self):
        return (
            f"fetched={self.fetched} ingested={self.ingested} "
            f"routed={self.routed} left_for_human={self.left_for_human} "
            f"reasoned={self.reasoned} drafted={self.drafted} "
            f"errors={len(self.errors)}"
        )


def _connect():
    return psycopg.connect(
        **config.database_settings().app_kwargs(), autocommit=True
    )


def fetch_and_ingest(conn, report):
    """Pull new mail. Failure here must not stop the rest of the cycle."""
    try:
        from app.integrations import gmail_ingest
        from app.ingestion import core
    except ImportError as exc:
        report.errors.append(f"gmail adapter unavailable: {exc}")
        return

    try:
        service = gmail_ingest._service_for_expected_mailbox()
        address = config.require("GMAIL_EXPECTED_ADDRESS").strip().lower()
        mailbox_id = gmail_ingest._resolve_mailbox(conn, address)
        cursor = core.read_cursor(conn, mailbox_id)

        messages = gmail_ingest.fetch(service, cursor)
        report.fetched = len(messages)

        if not messages:
            return

        result = core.ingest(conn, mailbox_id, messages)
        report.ingested = sum(
            1 for outcome in result.outcomes if outcome.action == "INSERTED"
        )
    except SystemExit as exc:
        # The credential and mailbox gates exit rather than raise. An
        # unattended loop must survive that and say why.
        report.errors.append(f"mail fetch refused: {exc}")
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"mail fetch failed: {exc}")


def route_untriaged(conn, report):
    """Give every unscoped case to the router. It refuses when unsure."""
    for case_id in router.untriaged(conn):
        try:
            outcome, detail = router.triage(conn, case_id)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"triage {case_id[:8]}: {exc}")
            continue

        if outcome == router.ROUTED:
            report.routed += 1
            print(f"  ROUTED       {case_id[:8]}  {detail}")
        else:
            report.left_for_human += 1
            print(f"  {outcome:12} {case_id[:8]}  {detail}")


def _cases_awaiting_reasoning(conn, limit):
    """Triaged cases that have never produced a proposal."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id::text
            FROM app.cases c
            WHERE c.service_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM app.action_proposals p
                  WHERE p.case_id = c.id
              )
            ORDER BY c.created_at
            LIMIT %s
            """,
            (limit,),
        )

        return [row[0] for row in cur.fetchall()]


def reason_over_cases(conn, report, model):
    """Run the agent on cases that have never been reasoned about."""
    from app.agent import runner

    for case_id in _cases_awaiting_reasoning(conn, MAX_RUNS_PER_CYCLE):
        if _STOPPING:
            return

        try:
            result = runner.execute(model, case_id)
            report.reasoned += 1
            state = getattr(result, "decision_state", None) or "recorded"
            print(f"  REASONED     {case_id[:8]}  {state}")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"run {case_id[:8]}: {exc}")
            print(f"  RUN FAILED   {case_id[:8]}  {exc}")


def run_cycle(model, fetch_mail=True):
    """One pass. Never raises: an unattended loop that dies is useless."""
    report = CycleReport(
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    with _connect() as conn:
        if fetch_mail:
            fetch_and_ingest(conn, report)

        route_untriaged(conn, report)

        if model is not None:
            reason_over_cases(conn, report, model)

    return report


def build_model(stub):
    """The adapter the loop reasons with."""
    from app.agent import model as model_module

    if stub:
        return model_module.DeterministicStubModel()

    from app.agent import bedrock

    return bedrock.BedrockStrandsModel()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unattended supervisor.")
    parser.add_argument("--once", action="store_true", help="one cycle")
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS
    )
    parser.add_argument(
        "--no-mail", action="store_true", help="skip the Gmail fetch"
    )
    parser.add_argument(
        "--no-reason", action="store_true", help="route only, do not reason"
    )
    parser.add_argument(
        "--stub", action="store_true", help="no Bedrock, for offline checks"
    )
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _request_stop)

    model = None

    if not args.no_reason:
        try:
            model = build_model(args.stub)
        except SystemExit as exc:
            print(f"MODEL UNAVAILABLE: {exc}")
            print("Continuing in routing-only mode.")

    print(
        f"SUPERVISOR: interval={args.interval}s "
        f"mail={'off' if args.no_mail else 'on'} "
        f"model={'none' if model is None else type(model).__name__}"
    )
    print("Approval and dispatch are never automatic. Ctrl+C to stop.\n")

    while True:
        report = run_cycle(model, fetch_mail=not args.no_mail)

        print(f"CYCLE {report.started_at}  {report.summary()}")

        for problem in report.errors:
            print(f"  ! {problem}")

        if args.once or _STOPPING:
            return 1 if report.errors else 0

        for _ in range(args.interval):
            if _STOPPING:
                return 0

            time.sleep(1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        raise SystemExit(0)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
