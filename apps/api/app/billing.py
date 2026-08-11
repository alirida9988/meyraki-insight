"""Who is entitled to run an analysis, and why.

Deliberately provider-agnostic. Checkout pages and webhook payloads differ between
Stripe, Paddle and Lemon Squeezy, and which of those is even available depends on where
the selling entity is registered — Stripe does not onboard sellers in every country this
product is being launched from. So the part that decides entitlement is written once,
here, and the provider adapter plugs in later without touching it.

Two properties matter more than the arithmetic:

- **Off by default.** With no `MEYRAKI_BILLING=on`, every organisation is unlimited. A
  half-configured paywall that starts refusing analyses during a client demo would be a
  self-inflicted outage, and a pilot has no business being metered.
- **Refusals name the reason and the remedy.** "Payment required" tells a studio nothing;
  it needs to know it has used its three free analyses and what to do about that.

An analysis is the unit billed because it is the unit that costs — roughly $0.25 of model
spend each — so the meter and the expense line move together instead of drifting apart.
"""

import os
from dataclasses import dataclass

FREE_PLAN = "free"
STUDIO_PLAN = "studio"

# Enough to evaluate the product on real plans without paying, and few enough that a
# studio using it in earnest reaches the decision point quickly.
DEFAULT_FREE_ANALYSES = 3

ACTIVE_STATUSES = frozenset({"active", "trialing"})


def enabled() -> bool:
    """True only when billing has been switched on deliberately."""
    return os.environ.get("MEYRAKI_BILLING", "").strip().lower() == "on"


def free_allowance() -> int:
    raw = os.environ.get("MEYRAKI_FREE_ANALYSES", "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_FREE_ANALYSES
    except ValueError:
        # A typo in configuration must not silently mean zero, which would lock every
        # organisation out of a product they are entitled to use.
        return DEFAULT_FREE_ANALYSES


@dataclass(frozen=True)
class Entitlement:
    allowed: bool
    reason: str
    plan: str
    used: int
    included: int
    credits: int
    subscription_status: str | None

    @property
    def remaining_free(self) -> int:
        return max(0, self.included - self.used)


def evaluate(
    *,
    plan: str | None,
    subscription_status: str | None,
    credits: int,
    analyses_used: int,
) -> Entitlement:
    """Decide whether one more analysis may start, and say why either way.

    Order of precedence is deliberate: an active subscription is unlimited, then the free
    allowance, then purchased credits. Spending a one-off credit while a subscription is
    active would charge a studio twice for the same analysis.
    """
    plan = plan or FREE_PLAN
    included = free_allowance()

    def result(allowed: bool, reason: str) -> Entitlement:
        return Entitlement(
            allowed=allowed,
            reason=reason,
            plan=plan,
            used=analyses_used,
            included=included,
            credits=credits,
            subscription_status=subscription_status,
        )

    if not enabled():
        return result(True, "Billing is not enabled on this deployment.")

    if plan == STUDIO_PLAN and (subscription_status or "") in ACTIVE_STATUSES:
        return result(True, "Studio subscription is active.")

    if analyses_used < included:
        left = included - analyses_used
        return result(True, f"{left} of {included} included analyses remaining.")

    if credits > 0:
        return result(True, f"{credits} purchased analysis credit(s) available.")

    # Past due is worth naming separately: the studio believes it is a paying customer,
    # and "you have used your free analyses" would be both wrong and insulting.
    if (subscription_status or "") == "past_due":
        return result(
            False,
            "Your subscription payment did not go through. Update your payment details "
            "to continue running analyses.",
        )

    return result(
        False,
        f"You have used all {included} included analyses. Subscribe, or buy a single "
        "analysis, to continue.",
    )


def consumes_credit(entitlement: Entitlement) -> bool:
    """Whether starting this analysis should decrement a purchased credit.

    Only when the credit is what permitted it — an unlimited subscription or a remaining
    free analysis must not silently eat one a studio paid for.
    """
    if not enabled() or not entitlement.allowed:
        return False
    if entitlement.plan == STUDIO_PLAN and (
        entitlement.subscription_status or ""
    ) in ACTIVE_STATUSES:
        return False
    if entitlement.used < entitlement.included:
        return False
    return entitlement.credits > 0
