"""Grant a plan or analysis credits by hand.

    docker compose -f docker-compose.yml -f docker-compose.tunnel.yml \
      exec api python -m app.grant --list
    ... exec api python -m app.grant --email studio@example.com --plan studio
    ... exec api python -m app.grant --email studio@example.com --credits 5

Pilots are invoiced by agreement rather than by checkout, so entitlement is granted here
instead of by a payment webhook. A command run inside the API container is the whole
mechanism: no admin role, no privileged endpoint, and therefore no new way in for anyone
who is not already on the host.

It prints the before and after state of every change. Granting the wrong studio a plan is
recoverable; not noticing you did is what turns it into a billing dispute.
"""

import argparse
import sys

from sqlalchemy import func, select

from . import billing
from .db import SessionLocal
from .models import Analysis, Org, Project, User


def _usage(session, org_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(Analysis.id))
            .join(Project, Project.id == Analysis.project_id)
            .where(Project.org_id == org_id)
        )
        or 0
    )


def _describe(session, org: Org) -> str:
    used = _usage(session, org.id)
    users = session.scalars(select(User.email).where(User.org_id == org.id)).all()
    return (
        f"{org.name!r} <{', '.join(users) or 'no users'}> "
        f"plan={org.plan} status={org.subscription_status or '-'} "
        f"credits={org.analysis_credits} analyses_used={used}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grant a plan or analysis credits.")
    parser.add_argument("--email", help="any user in the organisation to change")
    parser.add_argument("--plan", choices=[billing.FREE_PLAN, billing.STUDIO_PLAN])
    parser.add_argument(
        "--status",
        choices=["active", "trialing", "past_due", "canceled", "none"],
        help="subscription status; 'none' clears it",
    )
    parser.add_argument("--credits", type=int, help="ADD this many one-off analysis credits")
    parser.add_argument("--list", action="store_true", help="show every organisation and stop")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        if args.list or not args.email:
            for org in session.scalars(select(Org).order_by(Org.created_at)).all():
                print(" ", _describe(session, org))
            if not args.email:
                print("\nPass --email to change one. Nothing was modified.")
            return 0

        user = session.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            print(f"No user with email {args.email!r}. Nothing was modified.")
            return 1
        org = session.get(Org, user.org_id)
        if org is None:
            print("That user has no organisation, which should be impossible.")
            return 1

        print("before:", _describe(session, org))

        if args.plan:
            org.plan = args.plan
            # A studio plan with no status would be refused by the entitlement check,
            # which reads as "I granted it and it did not work". Default it to active and
            # say so, rather than leaving a half-granted plan behind.
            if args.plan == billing.STUDIO_PLAN and not args.status and not org.subscription_status:
                org.subscription_status = "active"
                print("note: subscription_status set to 'active' — a studio plan without it grants nothing")
        if args.status:
            org.subscription_status = None if args.status == "none" else args.status
        if args.credits:
            # Additive on purpose: granting five credits twice should mean ten, not five.
            org.analysis_credits = max(0, org.analysis_credits + args.credits)

        if not (args.plan or args.status or args.credits):
            print("Nothing to change — pass --plan, --status or --credits.")
            return 0

        session.commit()
        session.refresh(org)
        print("after: ", _describe(session, org))

        e = billing.evaluate(
            plan=org.plan,
            subscription_status=org.subscription_status,
            credits=org.analysis_credits,
            analyses_used=_usage(session, org.id),
        )
        # Report the decision this grant actually produces, not the fields that were
        # written. The two can differ, and the field values are not what the studio meets.
        print(f"result: can start an analysis = {e.allowed} — {e.reason}")
        if not billing.enabled():
            print("note:   billing is not enforced on this deployment (MEYRAKI_BILLING is not 'on')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
