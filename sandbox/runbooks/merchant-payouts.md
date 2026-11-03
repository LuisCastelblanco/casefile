# Runbook: Merchant payouts (payout-service -> ledger-api)

Owner: merchant-platform (dev), core-ledger (ledger-api), L2 payments (first response)

## Flow
The nightly batch (02:00 local) creates one row in `payouts` per merchant and posts it to `ledger-api`, which writes `ledger_postings`.
Every posting MUST carry an idempotency key so retries cannot double-post. ledger-api reads the key from the `Idempotency-Key` header and stores it in `ledger_postings.idempotency_key`.

## Duplicate payout triage
- Find merchants with more than one ledger posting for the same `payout_id`.
- Check whether `ledger_postings.idempotency_key` is populated for the batch.
- Check payout-service logs for timeouts/retries and ledger-api latency for the batch window.
- ledger-api has a nightly maintenance window (index rebuild) around 02:10-02:15.

## Containment
Ask merchant-platform to pause the next batch. Reversals are done by finance-ops with the list of duplicated postings; L2 must provide merchant_id, payout_id, both posting_ids and amounts.
