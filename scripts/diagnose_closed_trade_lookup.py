"""Diagnose why get_closed_trade_details() fails in production.

READ-ONLY. Issues only GET requests against the OANDA v20 REST API for the
operator's own account. Places/cancels/modifies nothing.

The engine swallows the exception from get_closed_trade_details() and logs only
``closed_trade_details_unavailable`` with no error text, so the real cause is
invisible. This script replays the exact same flow but prints the actual HTTP
status code and error body OANDA returns.

MUST run on the prod box: the operator's OANDA credentials live only in the prod
SQLite DB, Fernet-encrypted with AUREX_CREDENTIAL_ENCRYPTION_KEY (per-user web
model — never in .env). It loads them via the app's own credential store, so the
token is never printed or copied. Identity (user_id) is resolved the same way as
analyse_run.py: --user-id flag or analysis.local.json (both gitignored for PII).

Usage (on prod, inside the app container/venv):
    python scripts/diagnose_closed_trade_lookup.py --user-id <id>
    python scripts/diagnose_closed_trade_lookup.py --user-id <id> --trade-id 10750

Environment expected (same as the running app):
    DB_PATH                          path to the SQLite DB
    AUREX_CREDENTIAL_ENCRYPTION_KEY  Fernet key to decrypt stored credentials
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = REPO_ROOT / "analysis.local.json"

_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
_CLOSE_LOOKBACK_TXNS = 200  # mirror OANDABrokerAdapter._CLOSE_LOOKBACK_TXNS


def resolve_user_id(args: argparse.Namespace) -> str:
    if args.user_id:
        return str(args.user_id)
    if LOCAL_CONFIG.exists():
        cfg = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
        uid = cfg.get("user_id")
        if uid:
            return str(uid)
    sys.exit(
        "No identity. Pass --user-id or create analysis.local.json "
        '{"user_id": "..."} (gitignored). Never hardcode it in a tracked file.'
    )


def load_credentials(user_id: str):
    """Decrypt the operator's OANDA creds via the app's own credential store."""
    from aurex_trade.adapters.sqlite.credential_store import FernetCredentialStore

    db_path = os.environ.get("DB_PATH")
    if not db_path:
        sys.exit("DB_PATH not set. Run inside the prod app environment.")
    key = os.environ.get("AUREX_CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        sys.exit("AUREX_CREDENTIAL_ENCRYPTION_KEY not set. Run inside the prod app environment.")

    store = FernetCredentialStore(Path(db_path), key)
    creds = store.retrieve(user_id, "oanda")
    store.close()
    if creds is None:
        sys.exit(f"No OANDA credentials stored for user {user_id}.")
    return creds


def make_client(creds) -> httpx.Client:
    base_url = _BASE_URLS.get(creds.server)
    if base_url is None:
        sys.exit(f"Invalid server {creds.server!r}.")
    token = creds.access_token.strip().encode("ascii", "ignore").decode("ascii")
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    )


def show(label: str, resp: httpx.Response) -> dict:
    """Print status + a bounded body preview; return parsed JSON if any."""
    print(f"\n[{label}] GET {resp.request.url}")
    print(f"  -> HTTP {resp.status_code}")
    body: dict = {}
    try:
        body = resp.json()
    except Exception:
        print(f"  body (text): {resp.text[:500]}")
        return {}
    if resp.status_code >= 400:
        # This is the path the engine hides. Surface the OANDA error verbatim.
        print(f"  errorMessage: {body.get('errorMessage')}")
        print(f"  full body   : {json.dumps(body)[:800]}")
    return body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-id", help="Operator user_id (else analysis.local.json)")
    ap.add_argument("--account-id", help="Override account id (else from stored creds)")
    ap.add_argument(
        "--trade-id",
        help="Specific broker trade id to look up. If omitted, uses the most "
        "recently closed trade found in the transaction scan.",
    )
    args = ap.parse_args()

    user_id = resolve_user_id(args)
    creds = load_credentials(user_id)
    account_id = args.account_id or creds.account_id
    print(f"Account : ...{account_id[-7:]}  server={creds.server}")

    client = make_client(creds)

    # Step 1: /summary — the GUARDED call (returns lastTransactionID).
    summary_resp = client.get(f"/v3/accounts/{account_id}/summary")
    summary = show("step1 /summary", summary_resp)
    if summary_resp.status_code >= 400:
        print("\n==> /summary itself failed. The engine would log "
              "'oanda_last_txn_id_unavailable' and return None here.")
        return
    last_txn_id = int(summary["account"]["lastTransactionID"])
    print(f"  lastTransactionID = {last_txn_id}")

    # Step 2: /transactions/sinceid — the UNGUARDED call (the suspected failure).
    since_id = max(1, last_txn_id - _CLOSE_LOOKBACK_TXNS)
    print(f"\n  since_id = {last_txn_id} - {_CLOSE_LOOKBACK_TXNS} = {since_id}")
    sinceid_resp = client.get(
        f"/v3/accounts/{account_id}/transactions/sinceid",
        params={"id": str(since_id), "type": "ORDER_FILL"},
    )
    data = show("step2 /transactions/sinceid (type=ORDER_FILL)", sinceid_resp)

    if sinceid_resp.status_code >= 400:
        print("\n==> THIS is the call the engine swallows. The status/message "
              "above is the real cause of 'closed_trade_details_unavailable'.")
        # Probe variants to localize the exact trigger.
        probe_variants(client, account_id, last_txn_id, since_id)
        return

    txns = data.get("transactions", [])
    print(f"  transactions returned: {len(txns)}")
    closed = [
        (e.get("tradeID"), t.get("reason"), e.get("realizedPL"), e.get("price"))
        for t in txns
        for e in (t.get("tradesClosed") or [])
    ]
    print(f"  trade closures found in window: {len(closed)}")
    for tid, reason, pl, price in closed[-10:]:
        print(f"    tradeID={tid} reason={reason} realizedPL={pl} price={price}")

    target = args.trade_id or (closed[-1][0] if closed else None)
    if target is None:
        print("\n==> No closures in the scan window. Lookup returns None "
              "('oanda_closing_fill_not_found') for any trade — not an exception.")
        return
    match = [c for c in closed if c[0] == target]
    print(f"\n==> Lookup for tradeID={target}: "
          f"{'FOUND ' + str(match[-1]) if match else 'NOT in window (would return None)'}")


def probe_variants(client: httpx.Client, account_id: str, last_txn_id: int, since_id: int) -> None:
    """When sinceid 4xx's, narrow down whether it's the range, the count, or auth."""
    print("\n-- probing variants to localize the trigger --")
    # a) Same call without the type filter.
    show("probe a: sinceid no type filter",
         client.get(f"/v3/accounts/{account_id}/transactions/sinceid",
                    params={"id": str(since_id)}))
    # b) A trivially valid recent id (last_txn_id - 1).
    show("probe b: sinceid id=last-1",
         client.get(f"/v3/accounts/{account_id}/transactions/sinceid",
                    params={"id": str(max(1, last_txn_id - 1))}))
    # c) The /transactions range endpoint (reports count/pages, reveals limits).
    show("probe c: /transactions (range meta)",
         client.get(f"/v3/accounts/{account_id}/transactions"))


if __name__ == "__main__":
    main()
