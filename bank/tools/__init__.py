"""Tool registry: every tool call goes through bank.tools.runtime.ToolRuntime.call."""

from bank.tools import collections as _collections
from bank.tools import disputes, kyc, ops, payments, sales, support, underwriting

REGISTRY = {
    "support": support.TOOLS, "payments": payments.TOOLS, "disputes": disputes.TOOLS, "sales": sales.TOOLS,
    "collections": _collections.TOOLS, "kyc": kyc.TOOLS, "underwriting": underwriting.TOOLS, "ops": ops.TOOLS,
}
