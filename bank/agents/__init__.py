"""The eight banking agents: plain Python with seeded, probabilistic policies. No LLM calls."""

from bank.agents.base import BaseAgent, Task
from bank.agents.collections import CollectionsAgent
from bank.agents.disputes import DisputesAgent
from bank.agents.kyc import KycAgent
from bank.agents.ops import OpsAgent
from bank.agents.payments import PaymentsAgent
from bank.agents.sales import SalesAgent
from bank.agents.support import SupportAgent
from bank.agents.underwriting import UnderwritingAgent

AGENT_TYPES = ["support", "payments", "disputes", "sales", "collections", "kyc", "underwriting", "ops"]
AGENT_CLASSES = {
    "support": SupportAgent, "payments": PaymentsAgent, "disputes": DisputesAgent, "sales": SalesAgent,
    "collections": CollectionsAgent, "kyc": KycAgent, "underwriting": UnderwritingAgent, "ops": OpsAgent,
}
CUSTOMER_FACING = ("support", "payments", "disputes", "sales")


def agent_id(agent_type: str) -> str:
    return f"{agent_type}-01"


__all__ = ["AGENT_CLASSES", "AGENT_TYPES", "CUSTOMER_FACING", "BaseAgent", "Task", "agent_id"]
