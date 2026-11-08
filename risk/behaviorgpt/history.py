"""Sessions as BehaviorGPT histories (the portal API's format, oldest first).

Each action becomes a view of an item in an action catalog, `<agent_type>.<action>`
(e.g. `payments.send_ach`): the fixed vocabulary in shared/actions.md is the
catalog to upload. How much of an event goes into its item (policy_check,
auth_level, amount bands, ...) is the detector's call: change `item_id`.

Events here never carry `pressure_mode`; don't add it.
"""


def item_id(event: dict) -> str:
    return f"{event['agent_type']}.{event['action']}"


def to_history(session: list[dict]) -> list[dict]:
    return [{"type": "view", "item_id": item_id(e)} for e in session]
