from __future__ import annotations

from dataclasses import dataclass

from backend.tools.actions import CustomerActionTool
from backend.tools.customer_data import CustomerDataTool
from backend.tools.document_search import DocumentSearchTool


@dataclass(slots=True)
class AgentTools:
    document_search: DocumentSearchTool
    customer_data: CustomerDataTool
    customer_action: CustomerActionTool

    @property
    def names(self) -> list[str]:
        return [self.document_search.name, self.customer_data.name, self.customer_action.name]
