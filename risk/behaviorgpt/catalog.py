"""The action catalog: every action in shared/actions.md as a BehaviorGPT item.

BehaviorGPT ranks items by what they are (name, brand, categories), so each
action gets a plain description rather than its snake_case name. Item ids are
`<agent_type>.<action>`, the ids `history.item_id` puts in histories.

    python -m risk.behaviorgpt.catalog            # write risk/data/sundai_actions.parquet
    python -m risk.behaviorgpt.catalog --upload   # ...and embed it; prints UNBOXAI_CATALOG_ID

The file name is the catalog's name, so uploading again refreshes the same
catalog. Each API key holds 2 catalogs.
"""

import argparse
from pathlib import Path

from risk.behaviorgpt.history import item_id
from risk.ingest.load_logs import REPO_ROOT, load_action_vocab

DEFAULT_PATH = REPO_ROOT / "risk" / "data" / "sundai_actions.parquet"

ACTION_TEXT = {
    # support
    "get_balance": "Look up a customer's account balance",
    "update_contact": "Change a customer's phone number or email",
    "freeze_card": "Freeze a customer's card",
    "reset_password": "Reset a customer's online banking password",
    # payments
    "add_beneficiary": "Add a new payee to a customer's account",
    "confirm_2fa": "Confirm a one-time 2FA code with the customer",
    "send_ach": "Send an ACH bank transfer to a payee",
    "send_wire": "Send a wire transfer to a payee",
    # disputes
    "open_dispute": "Open a dispute on a card transaction",
    "fetch_txn": "Pull the disputed transaction's details",
    "issue_credit": "Issue a credit or refund to the customer",
    "close_case": "Close the dispute case",
    # sales
    "get_profile": "Read the customer's profile and products",
    "recommend_product": "Recommend a banking product to the customer",
    "open_account": "Open a new account for the customer",
    # collections
    "get_delinquency": "Look up a customer's overdue balance",
    "send_message": "Send a collections message to the customer",
    "offer_plan": "Offer the customer a repayment plan",
    "waive_fee": "Waive a late fee",
    # kyc
    "review_alert": "Open and review an anti-money-laundering alert",
    "fetch_history": "Pull the customer's transaction history",
    "escalate": "Escalate the alert to the compliance officer",
    "file_sar": "File a suspicious activity report",
    "dismiss_alert": "Dismiss the alert as a false positive",
    # underwriting
    "pull_credit": "Pull the applicant's credit report",
    "verify_income": "Verify the applicant's income documents",
    "score_applicant": "Score the loan application",
    "decide_loan": "Approve or decline the loan",
    # ops
    "match_ledger": "Reconcile ledger entries",
    "post_adjustment": "Post a manual ledger adjustment",
    "read_logs": "Read system audit logs",
    "request_access": "Request elevated system access",
}


def catalog_rows() -> list[dict]:
    rows = []
    for agent_type, actions in sorted(load_action_vocab().items()):
        for action in sorted(actions):
            rows.append(
                {
                    "id": item_id({"agent_type": agent_type, "action": action}),
                    "name": ACTION_TEXT.get(action, action.replace("_", " ").capitalize()),
                    "brand": f"Sundai Bank {agent_type} agent",
                    "categories": f"Banking, {agent_type.capitalize()}",
                    "event_type": "product",
                    "group": "product",
                    "keywords": [action, agent_type],
                }
            )
    return rows


def write_catalog(path: Path = DEFAULT_PATH) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from behaviorgpt.resources.catalogs import CATALOG_SCHEMA, check_catalog_schema

    table = pa.Table.from_pylist(
        [{col: row.get(col) for col in CATALOG_SCHEMA} for row in catalog_rows()],
        schema=pa.schema(CATALOG_SCHEMA),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    check_catalog_schema(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build (and upload) the action catalog")
    parser.add_argument("--upload", action="store_true", help="embed it with BehaviorGPT")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    path = write_catalog(args.path)
    print(f"wrote {len(catalog_rows())} actions to {path}")
    if args.upload:
        from behaviorgpt import UnboxAIClient
        from dotenv import load_dotenv

        load_dotenv()
        job = UnboxAIClient(market="us").embed(path, wait=True, timeout=1800.0)
        print(f"embedded. Add to .env:\nUNBOXAI_CATALOG_ID={job.catalog_id}")


if __name__ == "__main__":
    main()
