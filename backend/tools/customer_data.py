from __future__ import annotations

import re
from typing import Any

from backend.auth import ScopedRecordNotFound
from backend.database import Database
from backend.policy_engine import PolicyEngine
from backend.schemas import CustomerIdentity, Decision


class CustomerDataTool:
    """Account-scoped structured lookup and deterministic calculation tool."""

    name = "customer_data_lookup_and_calculation"

    def __init__(self, db: Database, policies: PolicyEngine):
        self.db = db
        self.policies = policies

    def account(self, identity: CustomerIdentity) -> dict[str, Any]:
        row = self.db.scoped_account(identity.account_id)
        if row is None:
            raise ScopedRecordNotFound("Record not found in your account")
        return row

    def order(self, identity: CustomerIdentity, order_id: str) -> dict[str, Any]:
        row = self.db.scoped_order(identity.account_id, order_id)
        if row is None:
            raise ScopedRecordNotFound("Record not found in your account")
        return row

    def ticket(self, identity: CustomerIdentity, ticket_id: str) -> dict[str, Any]:
        row = self.db.scoped_ticket(identity.account_id, ticket_id)
        if row is None:
            raise ScopedRecordNotFound("Record not found in your account")
        return row

    def has_foreign_account_reference(self, identity: CustomerIdentity, message: str) -> bool:
        lower = message.lower()
        explicit_ids = {value.upper() for value in re.findall(r"\bACCT-\d+\b", message, flags=re.I)}
        if any(account_id != identity.account_id for account_id in explicit_ids):
            return True
        for account in self.db.fetch_all("SELECT account_id, account_name FROM accounts"):
            if account["account_id"] == identity.account_id:
                continue
            aliases = {account["account_name"].lower(), account["account_name"].split()[0].lower()}
            if any(re.search(rf"\b{re.escape(alias)}\b", lower) for alias in aliases):
                return True
        return False

    def calculate_cancellation(self, identity: CustomerIdentity, order_id: str) -> Decision:
        account = self.account(identity)
        return self.policies.cancellation(self.order(identity, order_id), account)

    def calculate_service_credit(self, identity: CustomerIdentity, order_id: str) -> Decision:
        account = self.account(identity)
        return self.policies.service_credit(self.order(identity, order_id), account)

    def calculate_sla(self, identity: CustomerIdentity, ticket_id: str) -> Decision:
        account = self.account(identity)
        return self.policies.sla(self.ticket(identity, ticket_id), account)
