from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backend.schemas import Confidence, Decision

ASSESSMENT_TZ = ZoneInfo("Asia/Kolkata")


def _parse_local(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.replace(" Asia/Kolkata", "").strip()
    parsed = datetime.fromisoformat(cleaned)
    return parsed.replace(tzinfo=ASSESSMENT_TZ) if parsed.tzinfo is None else parsed.astimezone(ASSESSMENT_TZ)


class PolicyEngine:
    def __init__(self, rules_path: Path, dataset_snapshot: str):
        self.rules = json.loads(rules_path.read_text(encoding="utf-8"))
        self.snapshot_text = dataset_snapshot
        snapshot = _parse_local(dataset_snapshot)
        if snapshot is None:
            raise ValueError("Dataset snapshot is required")
        self.snapshot = snapshot

    @staticmethod
    def _decision(summary: str, **overrides: Any) -> Decision:
        outcome = overrides.pop("outcome", "answer")
        return Decision(outcome=outcome, summary=summary, **overrides)

    def active_agreement(self, account_id: str, at: datetime | None = None) -> dict[str, Any] | None:
        agreement = self.rules.get("agreements", {}).get(account_id)
        if not agreement or agreement.get("status") != "ACTIVE":
            return None
        day = (at or self.snapshot).date().isoformat()
        return agreement if agreement["valid_from"] <= day <= agreement["valid_to"] else None

    def order_status(self, order: dict[str, Any], account: dict[str, Any]) -> Decision:
        facts = [
            f"{order['order_id']} belongs to {account['account_name']}.",
            f"Carrier: {order['carrier']}.",
            f"Pickup window: {order['pickup_window_start']} to {order['pickup_window_end']}.",
        ]
        if order.get("pickup_actual_at"):
            facts.append(f"Recorded pickup: {order['pickup_actual_at']}.")
        else:
            facts.append("No pickup time is recorded in the supplied dataset snapshot.")
        return self._decision(
            f"{order['order_id']} is currently {order['status']}.",
            facts=facts,
            fields={"status": order["status"]},
        )

    def cancellation(self, order: dict[str, Any], account: dict[str, Any]) -> Decision:
        status = str(order["status"]).upper()
        requested_at = _parse_local(order.get("cancellation_requested_at")) or self.snapshot
        booked_at = _parse_local(order.get("booked_at"))
        agreement = self.active_agreement(account["account_id"], requested_at)
        sop = self.rules["cancellation"]["source_file"]
        facts = [
            f"{order['order_id']} is {status} in the supplied dataset.",
            f"The cancellation is evaluated at {order.get('cancellation_requested_at') or self.snapshot_text}.",
        ]
        if status == "DRAFT":
            return self._decision(
                "Yes. A DRAFT shipment may be cancelled with no fee.",
                facts=facts,
                source_files=[sop],
                fields={"cancellable": True, "fee_inr": 0},
            )
        if status == "PICKED_UP":
            return self._decision(
                "No. This shipment has already been picked up; use the return-to-origin workflow instead.",
                facts=facts,
                source_files=[sop],
                fields={"cancellable": False, "workflow": "return-to-origin"},
            )
        if status == "DELIVERED":
            return self._decision(
                "No. A delivered shipment cannot be cancelled.",
                facts=facts,
                source_files=[sop],
                fields={"cancellable": False},
            )
        if status != "BOOKED":
            return self._decision(
                "The supplied policy does not define cancellation behavior for this order status.",
                outcome="clarify",
                facts=facts,
                uncertainty=["Support must verify the status before any action."],
                confidence=Confidence.LOW,
                source_files=[sop],
            )
        if agreement and agreement.get("booked_cancellation_fee_waived"):
            return self._decision(
                "Yes. This BOOKED shipment can be cancelled before pickup with no cancellation fee.",
                facts=facts + ["The active customer agreement waives the BOOKED-stage fee regardless of booking age."],
                source_files=[agreement["source_file"], sop],
                fields={"cancellable": True, "fee_inr": 0, "agreement_override": True},
            )
        if booked_at is None:
            return self._decision(
                "I cannot calculate the cancellation fee because the booking time is missing.",
                outcome="clarify",
                facts=facts,
                uncertainty=["Booking time must be verified."],
                confidence=Confidence.LOW,
                source_files=[sop],
            )
        elapsed_minutes = max(0.0, (requested_at - booked_at).total_seconds() / 60)
        free_minutes = self.rules["cancellation"]["booked_free_minutes"]
        fee = 0 if elapsed_minutes <= free_minutes else self.rules["cancellation"]["booked_fee_inr"]
        summary = (
            "Yes. This BOOKED shipment can be cancelled with no cancellation fee."
            if fee == 0
            else f"Yes, but the applicable cancellation fee is INR {fee}."
        )
        return self._decision(
            summary,
            facts=facts,
            calculations=[
                f"Elapsed time since booking: {elapsed_minutes:.0f} minutes.",
                f"Current rule: no fee at or before {free_minutes} minutes; otherwise INR {self.rules['cancellation']['booked_fee_inr']}.",
            ],
            source_files=[sop],
            fields={"cancellable": True, "fee_inr": fee, "agreement_override": False},
        )

    def cancellation_guidance(self, account: dict[str, Any]) -> Decision:
        agreement = self.active_agreement(account["account_id"])
        sop = self.rules["cancellation"]["source_file"]
        if agreement and agreement.get("booked_cancellation_fee_waived"):
            return self._decision(
                "Your active agreement allows any BOOKED shipment to be cancelled before pickup without a cancellation fee, regardless of booking age.",
                facts=["After pickup, the return-to-origin workflow applies."],
                uncertainty=["Provide an order ID for a record-specific status check."],
                confidence=Confidence.MEDIUM,
                source_files=[agreement["source_file"], sop],
            )
        return self._decision(
            "The current default rule allows a BOOKED shipment to be cancelled before pickup: no fee within 30 minutes, then INR 250.",
            facts=["After pickup, the return-to-origin workflow applies."],
            uncertainty=["Provide an order ID so the status and booking time can be verified."],
            confidence=Confidence.MEDIUM,
            source_files=[sop],
        )

    def service_credit(self, order: dict[str, Any], account: dict[str, Any]) -> Decision:
        window_end = _parse_local(order.get("pickup_window_end"))
        pickup_actual = _parse_local(order.get("pickup_actual_at"))
        sop = self.rules["service_credit"]["source_file"]
        if window_end is None:
            return self._decision(
                "I cannot calculate the pickup delay because the pickup-window end is missing.",
                outcome="clarify",
                uncertainty=["Pickup timing must be verified."],
                confidence=Confidence.LOW,
                source_files=[sop],
            )
        delay_hours = max(0.0, ((pickup_actual or self.snapshot) - window_end).total_seconds() / 3600)
        carrier_fault = bool(order.get("carrier_fault"))
        customer_fault = bool(order.get("customer_fault"))
        facts = [
            f"{order['order_id']} is {delay_hours:.2f} hours past the pickup-window end at the dataset snapshot.",
            f"Carrier fault is {'confirmed' if carrier_fault else 'not confirmed'}.",
            f"Customer fault is {'present' if customer_fault else 'not present'}.",
        ]
        agreement = self.active_agreement(account["account_id"])
        applicable = (agreement or {}).get("service_credit") or self.rules["service_credit"]["default"]
        source_files = [sop]
        if agreement and agreement.get("service_credit"):
            source_files.insert(0, agreement["source_file"])
        threshold = float(applicable["delay_hours_strictly_more_than"])
        calculations = [
            f"Eligibility requires a delay strictly greater than {threshold:g} hours; observed delay is {delay_hours:.2f} hours."
        ]
        if not carrier_fault or customer_fault:
            return self._decision(
                "No. The supplied facts do not satisfy the service-credit fault conditions.",
                facts=facts,
                calculations=calculations,
                source_files=source_files,
                fields={"eligible": False, "delay_hours": delay_hours},
            )
        if delay_hours <= threshold:
            return self._decision(
                "No. The pickup delay does not pass the applicable strict delay threshold.",
                facts=facts,
                calculations=calculations,
                source_files=source_files,
                fields={"eligible": False, "delay_hours": delay_hours, "threshold_hours": threshold},
            )
        if applicable.get("fixed_inr") is not None:
            amount = int(applicable["fixed_inr"])
            calculations.append(f"The active agreement replaces the default formula with a fixed INR {amount} credit.")
        else:
            percentage = float(order["shipment_fee_inr"]) * float(applicable["fee_percentage"])
            amount = min(float(applicable["max_inr"]), percentage)
            amount = int(amount) if amount.is_integer() else round(amount, 2)
            calculations.append(
                f"Credit = lower of INR {applicable['max_inr']} or {float(applicable['fee_percentage']) * 100:g}% of INR {float(order['shipment_fee_inr']):.2f} = INR {amount}."
            )
        uncertainty: list[str] = []
        if agreement and agreement.get("monthly_credit_cap_inr") is not None:
            uncertainty.append(
                f"The agreement caps monthly aggregate credits at INR {agreement['monthly_credit_cap_inr']}, but issued-to-date credits are not supplied. Support must verify the remaining cap before issuing the credit."
            )
            source_files.insert(0, agreement["source_file"])
        return self._decision(
            f"The supplied facts make this pickup eligible for an INR {amount} service credit.",
            facts=facts,
            calculations=calculations,
            uncertainty=uncertainty,
            confidence=Confidence.MEDIUM if uncertainty else Confidence.HIGH,
            source_files=list(dict.fromkeys(source_files)),
            fields={"eligible": True, "amount_inr": amount, "delay_hours": delay_hours, "threshold_hours": threshold},
        )

    def service_credit_guidance(self, account: dict[str, Any], stated_delay_hours: float | None) -> Decision:
        agreement = self.active_agreement(account["account_id"])
        applicable = (agreement or {}).get("service_credit") or self.rules["service_credit"]["default"]
        threshold = float(applicable["delay_hours_strictly_more_than"])
        source_files = [self.rules["service_credit"]["source_file"]]
        if agreement and agreement.get("service_credit"):
            source_files.insert(0, agreement["source_file"])
        if stated_delay_hours is not None and stated_delay_hours <= threshold:
            return self._decision(
                f"No. A {stated_delay_hours:g}-hour delay does not pass your applicable threshold, which requires more than {threshold:g} hours.",
                facts=["Carrier fault must also be confirmed and there must be no customer-caused issue."],
                source_files=source_files,
                fields={"eligible_from_stated_delay": False},
            )
        return self._decision(
            (
                f"Your applicable policy requires a pickup delay strictly greater than {threshold:g} hours."
                if stated_delay_hours is None
                else f"A {stated_delay_hours:g}-hour delay passes the time threshold, but the other facts still need verification."
            ),
            outcome="clarify",
            facts=["Carrier fault must be confirmed and there must be no customer-caused issue."],
            uncertainty=["Provide the order ID so timing, fault, and the amount can be verified."],
            confidence=Confidence.MEDIUM,
            source_files=source_files,
        )

    @staticmethod
    def classify_ticket(ticket: dict[str, Any]) -> str:
        text = f"{ticket['subject']} {ticket['description']}".lower()
        if re.search(r"api key exposure|credential exposure|security incident|production api key", text):
            return "P1"
        if re.search(r"every user|all shipment creation|complete outage", text) and re.search(
            r"fail|http 500|outage", text
        ):
            return "P1"
        if re.search(r"bulk upload|major feature|materially degraded", text) and re.search(
            r"fail|unavailable|error", text
        ):
            return "P2"
        if re.search(r"how do|configuration|billing contact|minor", text):
            return "P3"
        return "P2" if re.search(r"fail|degraded|not working", text) else "P3"

    def sla(self, ticket: dict[str, Any], account: dict[str, Any]) -> Decision:
        severity = self.classify_ticket(ticket)
        agreement = self.active_agreement(account["account_id"])
        source_files = [self.rules["support"]["source_file"]]
        target = self.rules["support"]["default_targets"][account["plan"]][severity]
        if agreement and agreement.get("support_targets", {}).get(severity):
            target = agreement["support_targets"][severity]
            source_files.insert(0, agreement["source_file"])
        label = f"{target['minutes']} minutes" if target.get("minutes") is not None else target["label"]
        facts = [
            f"{ticket['ticket_id']} is classified as {severity} from the current definitions.",
            f"The applicable first-response target is {label}.",
        ]
        created = _parse_local(ticket.get("created_at"))
        if created is None:
            return self._decision(
                "The response target is known, but breach status cannot be calculated without ticket creation time.",
                outcome="clarify",
                facts=facts,
                uncertainty=["Ticket creation time is missing."],
                confidence=Confidence.LOW,
                source_files=source_files,
            )
        elapsed_minutes = (self.snapshot - created).total_seconds() / 60
        if target.get("minutes") is None:
            return self._decision(
                "The applicable target is stated in business time, so exact breach status cannot be calculated from the supplied pack.",
                outcome="clarify",
                facts=facts,
                calculations=[f"Wall-clock age at the dataset snapshot is {elapsed_minutes:.0f} minutes."],
                uncertainty=["Business hours, weekends, and holidays are not defined."],
                confidence=Confidence.MEDIUM,
                source_files=source_files,
                fields={"severity": severity, "breached": None},
            )
        breached = elapsed_minutes > float(target["minutes"])
        summary = (
            f"The {severity} first-response target is breached; escalation is recommended."
            if breached
            else f"The {severity} first-response target is not yet breached."
        )
        return self._decision(
            summary,
            outcome="escalation_recommended" if breached or severity == "P1" else "answer",
            facts=facts,
            calculations=[
                f"Wall-clock age is {elapsed_minutes:.0f} minutes versus a {target['minutes']}-minute target."
            ],
            source_files=source_files,
            fields={"severity": severity, "breached": breached, "target_minutes": target["minutes"]},
        )

    def ticket_status(self, ticket: dict[str, Any]) -> Decision:
        return self._decision(
            f"{ticket['ticket_id']} is currently {ticket['status']}.",
            facts=[
                f"Subject: {ticket['subject']}.",
                f"Created: {ticket['created_at']}.",
                f"Last customer message: {ticket['last_customer_message_at']}.",
            ],
            fields={"status": ticket["status"]},
        )

    def known_issue(self, ticket: dict[str, Any]) -> Decision | None:
        text = f"{ticket['subject']} {ticket['description']}".lower()
        source = "04_Product_Operations_Guide_and_Known_Issues.pdf"
        if "bulk upload" in text or (
            any(term in text for term in ("csv", "spreadsheet"))
            and any(term in text for term in ("fail", "dies", "error", "upload"))
        ):
            return self._decision(
                "This report matches current known issue KI-208: intermittent failures on large CSV bulk uploads.",
                facts=[
                    "The supported limit remains 5,000 rows.",
                    "The current workaround is to split the upload into files below 3,000 rows.",
                ],
                source_files=[source],
                fields={"known_issue": "KI-208"},
            )
        if "swiftship" in text and ("booked" in text or "pickup" in text):
            return self._decision(
                "This report matches monitored known issue KI-211: SwiftShip pickup-confirmation webhooks may arrive up to 20 minutes late.",
                facts=[
                    "Verify carrier status or wait through the known delay window before concluding that pickup did not occur."
                ],
                uncertainty=["The supplied ticket reports a likely match, not independent carrier confirmation."],
                confidence=Confidence.MEDIUM,
                source_files=[source],
                fields={"known_issue": "KI-211"},
            )
        return None

    def account_entitlement(self, account: dict[str, Any], message: str) -> Decision:
        facts = [f"Account: {account['account_name']} ({account['account_id']}).", f"Plan: {account['plan']}."]
        source_files = ["04_Product_Operations_Guide_and_Known_Issues.pdf"]
        if account.get("contract_file"):
            source_files.insert(0, account["contract_file"])
            facts.append(
                "An active customer agreement is supplied and takes precedence over defaults where it contains a specific term."
            )
        if re.search(r"bulk|csv|upload", message, re.IGNORECASE):
            included = account["plan"] in {"Growth", "Enterprise"}
            summary = (
                "Yes. Bulk Upload is included on your plan and supports up to 5,000 rows per CSV."
                if included
                else "No. Bulk Upload is not included on the Standard plan."
            )
            if included:
                facts.append(
                    "A current known issue can cause intermittent failures above approximately 3,000 rows; that workaround does not change the supported 5,000-row limit."
                )
            return self._decision(summary, facts=facts, source_files=source_files, fields={"bulk_upload": included})
        return self._decision(
            f"Your account is active on the {account['plan']} plan.",
            facts=facts + [f"Customer success manager: {account['csm']}."],
            source_files=source_files,
            fields={"plan": account["plan"], "premium_support": bool(account["premium_support"])},
        )

    def source_reliability(self, account: dict[str, Any]) -> Decision:
        files = ["01_Support_Policy_v3_CURRENT.pdf", "04_Product_Operations_Guide_and_Known_Issues.pdf"]
        if account.get("contract_file"):
            files.insert(0, account["contract_file"])
        return self._decision(
            "Current answers use a deterministic authority order: your active agreement first, then current policy/SOP, then current product documentation.",
            facts=[
                "Deprecated policy and historical ticket resolutions are context only and cannot override a current source."
            ],
            source_files=files,
            fields={"precedence": self.rules["source_precedence"]},
        )

    def historical_conflict(self, ticket: dict[str, Any], account: dict[str, Any]) -> Decision | None:
        resolution = str(ticket.get("historical_resolution") or "")
        if not resolution:
            return None
        lower = resolution.lower()
        agreement = self.active_agreement(account["account_id"])
        if agreement and agreement.get("booked_cancellation_fee_waived") and "250" in lower and "cancellation" in lower:
            return self._decision(
                "The historical resolution conflicts with the active customer agreement and must not be reused.",
                facts=[
                    f"Historical context said: {resolution}",
                    "The active agreement waives the BOOKED-stage cancellation fee before pickup.",
                ],
                uncertainty=["Support should correct the historical guidance if it is reused operationally."],
                confidence=Confidence.HIGH,
                source_files=[agreement["source_file"], self.rules["cancellation"]["source_file"]],
            )
        if ("only supports 3,000" in lower or "only supports 3000" in lower) and account["plan"] in {
            "Growth",
            "Enterprise",
        }:
            return self._decision(
                "The historical resolution is incorrect: it confused the temporary workaround with the supported product limit.",
                facts=[
                    f"Historical context said: {resolution}",
                    "The current supported limit is 5,000 rows; below 3,000 rows is only the KI-208 workaround.",
                ],
                source_files=["04_Product_Operations_Guide_and_Known_Issues.pdf"],
            )
        return self._decision(
            "A historical resolution exists, but it is not an authoritative current rule.",
            facts=[f"Historical context: {resolution}"],
            uncertainty=["Current policy or agreement evidence is required before relying on this past answer."],
            confidence=Confidence.LOW,
            source_files=[],
        )

    @staticmethod
    def merge(decisions: list[Decision]) -> Decision:
        if not decisions:
            return Decision(
                outcome="clarify",
                summary="I can answer questions about your ParcelPilot account, orders, tickets, cancellations, pickup credits, support targets, and documented product issues.",
                uncertainty=["Please include an order ID, ticket ID, or a specific account/policy question."],
                confidence=Confidence.LOW,
            )
        outcome_order = {"blocked": 5, "action_prepared": 4, "clarify": 3, "escalation_recommended": 2, "answer": 1}
        outcome = max(decisions, key=lambda item: outcome_order[item.outcome]).outcome
        confidence = (
            Confidence.LOW
            if any(item.confidence == Confidence.LOW for item in decisions)
            else Confidence.MEDIUM
            if any(item.confidence == Confidence.MEDIUM for item in decisions)
            else Confidence.HIGH
        )
        return Decision(
            outcome=outcome,
            summary=" ".join(dict.fromkeys(item.summary for item in decisions)),
            facts=list(dict.fromkeys(fact for item in decisions for fact in item.facts)),
            calculations=list(dict.fromkeys(value for item in decisions for value in item.calculations)),
            uncertainty=list(dict.fromkeys(value for item in decisions for value in item.uncertainty)),
            confidence=confidence,
            source_files=list(dict.fromkeys(value for item in decisions for value in item.source_files)),
            fields={key: value for item in decisions for key, value in item.fields.items()},
        )
