# Runbook: Card settlement reconciliation (recon-engine)

Owner: payments-data (dev), L2 payments (first response)

## How it works
1. The card processor drops a settlement file (`PRC-SETTLE-YYYYMMDD.csv`) by 06:00 local time.
2. `settlement-ingest` parses it into `settlement_records`. Processor timestamps are always **UTC**.
   The ingest derives `settlement_business_date` from the processor timestamp converted to local time (America/Bogota, UTC-5).
3. `recon-engine` runs at 06:30 and matches `ledger_entries` to `settlement_records` by `txn_ref` **and business date**.
4. Unmatched items go to `recon_exceptions`. Normal volume: 0-5 per run. Alert threshold: 25.

## Common causes of an exception spike
- Late or partial processor file (check `processor_files`). recon-engine waits for the file, so a late file alone does not create exceptions unless the run started before arrival.
- Amount mismatches (FX or fee changes): reason `AMOUNT_MISMATCH`.
- Business-date drift: reason `NO_MATCH_SAME_BUSINESS_DATE`. Check whether the settlement business date of the affected items is shifted by one day relative to the ledger.

## Escalation
Escalate to **payments-data** with: count and reason breakdown, affected time window, sample txn_refs, and the deployment or config change suspected.
Do NOT manually edit `settlement_records`; reprocessing is done by dev with `reprocess --file <name>`.
