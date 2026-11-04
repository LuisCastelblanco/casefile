"""Build the synthetic "Kestrel Pay" production sandbox used by Casefile.

Everything here is invented: the company, services, customers, merchants,
incidents, logs and metrics. No real bank, customer or production data is used.

The sandbox holds three incidents whose root causes can only be proven by
combining evidence from several sources (SQL, logs, metrics, deployments,
config diffs), plus a red herring each, so the agent has to investigate and
rule things out instead of pattern-matching the ticket.

Run:  python -m sandbox.build_sandbox
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOCAL = timezone(timedelta(hours=-5))  # Kestrel Pay runs on America/Bogota time
UTC = timezone.utc

rng = random.Random(20261104)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def local(y, mo, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=LOCAL)


class Sandbox:
    def __init__(self) -> None:
        self.logs: dict[str, list[dict]] = {}
        self.metrics: dict[str, dict[str, list[list]]] = {}
        DATA.mkdir(parents=True, exist_ok=True)
        db_path = DATA / "kestrel.db"
        if db_path.exists():
            db_path.unlink()
        self.db = sqlite3.connect(db_path)
        self._schema()

    # ------------------------------------------------------------------ infra
    def log(self, service: str, ts: datetime, level: str, msg: str, **fields) -> None:
        entry = {"ts": iso(ts), "level": level, "service": service, "msg": msg}
        if fields:
            entry["fields"] = fields
        self.logs.setdefault(service, []).append(entry)

    def metric(self, service: str, name: str, ts: datetime, value: float) -> None:
        self.metrics.setdefault(service, {}).setdefault(name, []).append([iso(ts), round(value, 3)])

    def _schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE deployments (
              id TEXT PRIMARY KEY, service TEXT, version TEXT, deployed_at TEXT,
              author TEXT, change_ticket TEXT, summary TEXT);
            CREATE TABLE config_changes (
              id INTEGER PRIMARY KEY, deployment_id TEXT, service TEXT, key TEXT,
              old_value TEXT, new_value TEXT, changed_at TEXT);

            -- card settlement reconciliation
            CREATE TABLE ledger_entries (
              txn_ref TEXT PRIMARY KEY, ledger_ts_local TEXT, business_date TEXT,
              amount_cop INTEGER, merchant_id TEXT);
            CREATE TABLE settlement_records (
              id INTEGER PRIMARY KEY, txn_ref TEXT, processor_ts_utc TEXT,
              settlement_business_date TEXT, amount_cop INTEGER, source_file TEXT,
              ingested_at TEXT);
            CREATE TABLE recon_runs (
              run_date TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT,
              ledger_count INTEGER, settled_count INTEGER, matched INTEGER,
              exceptions INTEGER, status TEXT);
            CREATE TABLE recon_exceptions (
              id INTEGER PRIMARY KEY, run_date TEXT, txn_ref TEXT, reason TEXT,
              ledger_business_date TEXT, settlement_business_date TEXT);
            CREATE TABLE processor_files (
              file_name TEXT PRIMARY KEY, expected_at TEXT, arrived_at TEXT,
              record_count INTEGER);

            -- OTP notifications
            CREATE TABLE otp_messages (
              id INTEGER PRIMARY KEY, customer_id TEXT, channel TEXT, region TEXT,
              requested_at TEXT, enqueued_at TEXT, dispatched_at TEXT,
              provider_ack_at TEXT, provider_status TEXT);

            -- merchant payouts
            CREATE TABLE merchants (merchant_id TEXT PRIMARY KEY, name TEXT, tier TEXT);
            CREATE TABLE payouts (
              payout_id TEXT PRIMARY KEY, batch_id TEXT, merchant_id TEXT,
              amount_cop INTEGER, created_at TEXT, request_attempt INTEGER,
              client_version TEXT);
            CREATE TABLE ledger_postings (
              posting_id TEXT PRIMARY KEY, payout_id TEXT, merchant_id TEXT,
              amount_cop INTEGER, posted_at TEXT, idempotency_key TEXT);
            """
        )

    def deploy(self, dep_id, service, version, when, author, ticket, summary, changes=()):
        self.db.execute(
            "INSERT INTO deployments VALUES (?,?,?,?,?,?,?)",
            (dep_id, service, version, iso(when), author, ticket, summary),
        )
        for key, old, new in changes:
            self.db.execute(
                "INSERT INTO config_changes (deployment_id, service, key, old_value, new_value, changed_at)"
                " VALUES (?,?,?,?,?,?)",
                (dep_id, service, key, old, new, iso(when)),
            )
        self.log("deployer", when, "INFO", f"deployed {service} {version}", deployment_id=dep_id,
                 change_ticket=ticket, author=author)

    # ----------------------------------------------------------- background
    def background(self) -> None:
        """Routine, unrelated deployments and noise so the agent has to filter."""
        noise = [
            ("D-9101", "customer-portal", "5.8.2", local(2026, 10, 29, 14, 3), "web-team", "CHG-4402",
             "Copy changes on statements page", []),
            ("D-9104", "fraud-scoring", "1.22.0", local(2026, 10, 30, 11, 40), "risk-eng", "CHG-4410",
             "Retrain threshold refresh", [("score.block_threshold", "0.91", "0.90")]),
            ("D-9112", "customer-portal", "5.8.3", local(2026, 11, 1, 10, 15), "web-team", "CHG-4431",
             "Fix footer link", []),
            ("D-9118", "ledger-api", "7.4.1", local(2026, 11, 1, 16, 2), "core-ledger", "CHG-4440",
             "Dependency patch (CVE bump)", []),
            ("D-9121", "fraud-scoring", "1.22.1", local(2026, 11, 2, 9, 55), "risk-eng", "CHG-4450",
             "Logging verbosity", [("log.level", "INFO", "WARN")]),
        ]
        for d in noise:
            self.deploy(*d)
        for day in range(28, 32):
            for h in range(0, 24, 3):
                self.log("customer-portal", local(2026, 10, day if day <= 31 else 31, h, 7), "INFO",
                         "healthcheck ok", latency_ms=rng.randint(40, 90))

    # ------------------------------------------------- incident 1: recon tz
    def incident_recon(self) -> dict:
        business_date = "2026-10-30"
        ledger = []
        start = local(2026, 10, 30, 0, 0)
        n = 2431
        for i in range(n):
            # traffic weighted towards daytime; evenings still busy (e-commerce)
            hour = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 4, 6, 8, 9, 9, 9, 10, 9, 8, 8, 8, 9,
                                                   10, 11, 10, 8, 6, 3])[0]
            ts = start + timedelta(hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
            ledger.append((f"TX-{30001 + i}", ts, rng.randint(12, 950) * 1000, f"M-{rng.randint(1001, 2180)}"))
        ledger.sort(key=lambda r: r[1])
        # pick evening window that will break: ledger time 19:00-23:59 local == 00:00-04:59Z next day
        for ref, ts, amt, m in ledger:
            self.db.execute("INSERT INTO ledger_entries VALUES (?,?,?,?,?)",
                            (ref, iso(ts), business_date, amt, m))

        file_name = "PRC-SETTLE-20261031.csv"
        arrived = local(2026, 10, 31, 6, 14, 22)
        self.db.execute("INSERT INTO processor_files VALUES (?,?,?,?)",
                        (file_name, iso(local(2026, 10, 31, 6, 0)), iso(arrived), n))
        # previous days: files on time
        for d, arr in [(28, (5, 52)), (29, (5, 57)), (30, (5, 49))]:
            self.db.execute("INSERT INTO processor_files VALUES (?,?,?,?)",
                            (f"PRC-SETTLE-202610{d}.csv", iso(local(2026, 10, d, 6, 0)),
                             iso(local(2026, 10, d, *arr)), rng.randint(2200, 2500)))

        # ingest bug: processor UTC timestamps parsed as if they were local time
        exceptions = []
        ingest_at = local(2026, 10, 31, 6, 16, 3)
        for idx, (ref, ts, amt, m) in enumerate(ledger):
            proc_utc = ts.astimezone(UTC) + timedelta(seconds=rng.randint(1, 40))
            naive_as_local = proc_utc.replace(tzinfo=LOCAL)  # the bug
            settle_bd = naive_as_local.date().isoformat()
            self.db.execute(
                "INSERT INTO settlement_records (txn_ref, processor_ts_utc, settlement_business_date, amount_cop,"
                " source_file, ingested_at) VALUES (?,?,?,?,?,?)",
                (ref, proc_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), settle_bd, amt, file_name, iso(ingest_at)),
            )
            if settle_bd != business_date:
                exceptions.append((ref, settle_bd))
        for ref, sbd in exceptions:
            self.db.execute(
                "INSERT INTO recon_exceptions (run_date, txn_ref, reason, ledger_business_date,"
                " settlement_business_date) VALUES (?,?,?,?,?)",
                ("2026-10-31", ref, "NO_MATCH_SAME_BUSINESS_DATE", business_date, sbd),
            )
        n_exc = len(exceptions)
        # baseline runs
        for d, exc in [("2026-10-28", 2), ("2026-10-29", 0), ("2026-10-30", 1)]:
            cnt = rng.randint(2200, 2500)
            dd = int(d[-2:])
            self.db.execute("INSERT INTO recon_runs VALUES (?,?,?,?,?,?,?,?)",
                            (d, iso(local(2026, 10, dd, 6, 30)), iso(local(2026, 10, dd, 6, 41)), cnt, cnt,
                             cnt - exc, exc, "OK"))
            for k in range(exc):
                self.db.execute(
                    "INSERT INTO recon_exceptions (run_date, txn_ref, reason, ledger_business_date,"
                    " settlement_business_date) VALUES (?,?,?,?,?)",
                    (d, f"TX-{dd}0{k}9", "AMOUNT_MISMATCH", d, d))
        self.db.execute("INSERT INTO recon_runs VALUES (?,?,?,?,?,?,?,?)",
                        ("2026-10-31", iso(local(2026, 10, 31, 6, 30)), iso(local(2026, 10, 31, 6, 44)),
                         n, n, n - n_exc, n_exc, "THRESHOLD_BREACHED"))

        # the change that caused it
        dep_time = local(2026, 10, 30, 22, 5, 11)
        self.deploy("D-9107", "settlement-ingest", "2.14.0", dep_time, "payments-data", "CHG-4417",
                    "Parser refactor: unify timestamp handling across processor adapters",
                    [("parser.timestamp_source_tz", "UTC", "America/Bogota"),
                     ("parser.adapter", "legacy_csv_v1", "unified_csv_v2")])

        # logs
        S = "settlement-ingest"
        self.log(S, local(2026, 10, 30, 6, 2, 10), "INFO", "ingested processor file",
                 file="PRC-SETTLE-20261030.csv", records=2377, parser="legacy_csv_v1", source_tz="UTC")
        self.log(S, dep_time + timedelta(seconds=40), "INFO", "service started", version="2.14.0",
                 parser="unified_csv_v2", source_tz="America/Bogota")
        self.log(S, local(2026, 10, 31, 6, 0, 30), "WARN", "processor file not yet available, will retry",
                 expected=file_name)
        self.log(S, local(2026, 10, 31, 6, 10, 30), "WARN", "processor file not yet available, will retry",
                 expected=file_name)
        self.log(S, arrived, "INFO", "processor file arrived", file=file_name, sla_breach_minutes=14)
        self.log(S, ingest_at, "INFO", "ingested processor file", file=file_name, records=n,
                 parser="unified_csv_v2", source_tz="America/Bogota")
        R = "recon-engine"
        self.log(R, local(2026, 10, 31, 6, 30), "INFO", "recon run started", run_date="2026-10-31",
                 waited_for_file=True, file=file_name)
        self.log(R, local(2026, 10, 31, 6, 44), "ERROR", "recon run finished: exception threshold breached",
                 run_date="2026-10-31", matched=n - n_exc, exceptions=n_exc, threshold=25,
                 top_reason="NO_MATCH_SAME_BUSINESS_DATE")
        self.log(R, local(2026, 10, 30, 6, 41), "INFO", "recon run finished", run_date="2026-10-30",
                 exceptions=1, threshold=25)
        self.log("alerting", local(2026, 10, 31, 6, 44, 30), "ERROR",
                 "ALERT recon.exceptions.breach fired", value=n_exc, threshold=25, routed_to="l2-payments")
        self.log("alerting", local(2026, 10, 31, 6, 14, 30), "WARN",
                 "ALERT processor.file.late fired", minutes_late=14, routed_to="l2-payments")

        for d in range(28, 32):
            for h in range(0, 24):
                ts = local(2026, 10, d, h)
                self.metric(R, "exceptions_last_run", ts, 1 if d < 31 or h < 7 else n_exc)

        return {"exceptions": n_exc, "ledger": n}

    # ---------------------------------------------- incident 2: OTP latency
    def incident_otp(self) -> dict:
        change_at = local(2026, 11, 2, 8, 22, 4)
        self.deploy("D-9119", "notification-dispatcher", "config-only", change_at, "ops-automation",
                    "CHG-4447", "Night-time cost saving: scale consumer pool down outside business hours",
                    [("consumer.max_workers", "12", "2"),
                     ("consumer.scale_down_window", "00:00-06:00", "always")])
        D = "notification-dispatcher"
        start = local(2026, 11, 2, 7, 0)
        end = local(2026, 11, 2, 10, 30)
        backlog_s = 0.0
        lag_at_alert = 0.0
        t = start
        rows = []
        mid = 900000
        while t < end:
            per_min = rng.randint(55, 75) if t.hour >= 8 else rng.randint(25, 40)
            workers = 12 if t < change_at else 2
            capacity = workers * 29.0  # messages per minute the pool can dispatch
            backlog_s = max(0.0, backlog_s + (per_min - capacity) / capacity * 60)
            for _ in range(per_min):
                req = t + timedelta(seconds=rng.randint(0, 59))
                enq = req + timedelta(milliseconds=rng.randint(80, 300))
                q_delay = rng.uniform(0.8, 3.0) if t < change_at else rng.uniform(0.8, 3.0) + backlog_s
                disp = enq + timedelta(seconds=q_delay)
                ack = disp + timedelta(milliseconds=rng.randint(700, 1600))
                status = "delivered" if rng.random() > 0.002 else "failed"
                region = rng.choices(["CO", "PE", "EC"], weights=[80, 12, 8])[0]
                rows.append((mid, f"C-{rng.randint(100000, 199999)}", "sms", region, iso(req), iso(enq),
                             iso(disp), iso(ack), status))
                mid += 1
            if t.minute % 5 == 0:
                self.metric(D, "queue_depth", t, 0 if t < change_at else backlog_s * per_min / 60)
                self.metric(D, "active_workers", t, workers)
                self.metric(D, "dispatch_lag_p95_seconds", t, 2.6 if t < change_at else backlog_s + 2.9)
                self.metric("sms-gateway", "provider_latency_p95_ms", t, rng.uniform(1150, 1450))
                self.metric("sms-gateway", "provider_error_rate_pct", t, rng.uniform(0.1, 0.3))
            if t >= change_at and t.minute % 10 == 0:
                self.log(D, t, "WARN", "consumer lag above threshold", lag_seconds=round(backlog_s, 1),
                         threshold_seconds=30, active_workers=workers)
            if t == local(2026, 11, 2, 8, 52):
                lag_at_alert = backlog_s
            t += timedelta(minutes=1)
        self.db.executemany("INSERT INTO otp_messages VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self.log(D, change_at + timedelta(seconds=3), "INFO", "config reloaded", key="consumer.max_workers",
                 value=2, source="CHG-4447")
        self.log(D, change_at + timedelta(seconds=9), "INFO", "consumer pool resized", from_workers=12,
                 to_workers=2)
        self.log(D, local(2026, 11, 2, 6, 0), "INFO", "consumer pool resized", from_workers=12, to_workers=12,
                 note="schedule evaluation: no change")
        G = "sms-gateway"
        for k in range(0, 200, 9):
            ts = start + timedelta(minutes=k)
            self.log(G, ts, "INFO", "provider batch ack", provider="Altavoz SMS", http_status=200,
                     latency_ms=rng.randint(700, 1500), messages=rng.randint(40, 80))
        self.log("status-poller", local(2026, 11, 2, 8, 41), "WARN",
                 "external provider status changed", provider="Altavoz SMS",
                 status="Degraded performance", affected_regions="APAC (Singapore, Sydney)")
        self.log("status-poller", local(2026, 11, 2, 10, 2), "INFO", "external provider status changed",
                 provider="Altavoz SMS", status="Operational")
        self.log("alerting", local(2026, 11, 2, 8, 52), "ERROR", "ALERT otp.dispatch.lag fired",
                 p95_seconds=round(lag_at_alert + 2.9, 1), routed_to="l2-payments")
        return {"messages": len(rows), "lag_end_s": round(backlog_s)}

    # ------------------------------------------- incident 3: double payouts
    def incident_payouts(self) -> dict:
        merchants = []
        for i in range(1001, 2181):
            merchants.append((f"M-{i}", f"Comercio {i}", rng.choice(["standard", "standard", "plus"])))
        self.db.executemany("INSERT INTO merchants VALUES (?,?,?)", merchants)
        dep = local(2026, 11, 2, 18, 31, 47)
        self.deploy("D-9123", "payout-service", "3.1.0", dep, "merchant-platform", "CHG-4455",
                    "Migrate outbound HTTP to resilient-http client (timeouts + automatic retries)",
                    [("http.client", "requests-legacy", "resilient-http"),
                     ("http.timeout_seconds", "30", "5"),
                     ("http.retry_on_timeout", "false", "true"),
                     ("ledger.idempotency_header", "Idempotency-Key", "X-Idempotency-Key")])
        P, L = "payout-service", "ledger-api"
        # previous batch, healthy
        prev_start = local(2026, 11, 2, 2, 0)
        batch_prev, batch = "B-20261101", "B-20261102"
        pid = 500000
        lid = 800000
        for m, _, _ in merchants[:1180]:
            ts = prev_start + timedelta(seconds=rng.randint(0, 900))
            amt = rng.randint(80, 9000) * 1000
            self.db.execute("INSERT INTO payouts VALUES (?,?,?,?,?,?,?)",
                            (f"PO-{pid}", batch_prev, m, amt, iso(ts), 1, "3.0.4"))
            self.db.execute("INSERT INTO ledger_postings VALUES (?,?,?,?,?,?)",
                            (f"LP-{lid}", f"PO-{pid}", m, amt, iso(ts + timedelta(seconds=1)),
                             f"idem-{batch_prev}-{m}"))
            pid += 1
            lid += 1
        # current batch: ledger slow 02:10-02:14 (maintenance index rebuild)
        start = local(2026, 11, 3, 2, 0)
        slow_from, slow_to = local(2026, 11, 3, 2, 10), local(2026, 11, 3, 2, 14, 30)
        dupes = []
        for m, _, _ in merchants[:1180]:
            ts = start + timedelta(seconds=rng.randint(0, 900))
            amt = rng.randint(80, 9000) * 1000
            slow = slow_from <= ts <= slow_to
            self.db.execute("INSERT INTO payouts VALUES (?,?,?,?,?,?,?)",
                            (f"PO-{pid}", batch, m, amt, iso(ts), 1, "3.1.0"))
            # ledger receives header it does not recognise -> no idempotency key
            self.db.execute("INSERT INTO ledger_postings VALUES (?,?,?,?,?,?)",
                            (f"LP-{lid}", f"PO-{pid}", m, amt, iso(ts + timedelta(seconds=6 if slow else 1)),
                             None))
            lid += 1
            if slow and rng.random() < 0.62:
                retry_ts = ts + timedelta(seconds=5, milliseconds=rng.randint(10, 400))
                self.db.execute("INSERT INTO ledger_postings VALUES (?,?,?,?,?,?)",
                                (f"LP-{lid}", f"PO-{pid}", m, amt, iso(retry_ts + timedelta(seconds=4)), None))
                lid += 1
                dupes.append((m, amt, ts, f"PO-{pid}"))
                self.log(P, ts + timedelta(seconds=5), "WARN", "ledger call timed out, retrying",
                         payout_id=f"PO-{pid}", attempt=2, timeout_seconds=5,
                         header_sent="X-Idempotency-Key")
                self.log(P, retry_ts + timedelta(seconds=4), "INFO", "ledger posting confirmed",
                         payout_id=f"PO-{pid}", attempt=2)
            pid += 1
        self.log(P, dep + timedelta(seconds=30), "INFO", "service started", version="3.1.0",
                 http_client="resilient-http", retry_on_timeout=True)
        self.log(P, start, "INFO", "payout batch started", batch_id=batch, merchants=1180)
        self.log(P, start + timedelta(minutes=16), "INFO", "payout batch finished", batch_id=batch,
                 merchants=1180, errors=0)
        self.log(P, prev_start + timedelta(minutes=16), "INFO", "payout batch finished", batch_id=batch_prev,
                 merchants=1180, errors=0)
        self.log(L, local(2026, 11, 3, 2, 9, 50), "INFO", "maintenance: index rebuild started",
                 table="postings", window="02:10-02:15")
        self.log(L, local(2026, 11, 3, 2, 14, 40), "INFO", "maintenance: index rebuild finished")
        for k in range(6):
            self.log(L, start + timedelta(minutes=k * 3, seconds=12), "WARN",
                     "posting accepted without Idempotency-Key header (legacy mode)",
                     caller="payout-service", note="dedupe disabled for request")
        for k in range(0, 20):
            ts = start + timedelta(minutes=k)
            slow = slow_from <= ts <= slow_to
            self.metric(L, "request_latency_p95_ms", ts, rng.uniform(7800, 9600) if slow else rng.uniform(120, 260))
            self.metric(P, "ledger_call_retries", ts, rng.randint(4, 9) if slow else 0)
        total = sum(a for _, a, _, _ in dupes)
        self.log("support-desk", local(2026, 11, 3, 8, 12), "INFO", "ticket opened",
                 ticket="SUP-88213", subject="Merchant reports two identical deposits for 2 Nov settlement",
                 merchant=dupes[0][0] if dupes else None)
        return {"duplicates": len(dupes), "dup_total_cop": total}

    # ---------------------------------------------------------------- write
    def finish(self) -> None:
        self.db.commit()
        self.db.close()
        logdir = DATA / "logs"
        logdir.mkdir(exist_ok=True)
        for f in logdir.glob("*.jsonl"):
            f.unlink()
        for svc, entries in self.logs.items():
            entries.sort(key=lambda e: e["ts"])
            with open(logdir / f"{svc}.jsonl", "w", encoding="utf-8") as fh:
                for e in entries:
                    fh.write(json.dumps(e) + "\n")
        for series in self.metrics.values():
            for pts in series.values():
                pts.sort(key=lambda p: p[0])
        (DATA / "metrics.json").write_text(json.dumps(self.metrics), encoding="utf-8")


def build() -> dict:
    sb = Sandbox()
    sb.background()
    facts = {
        "recon": sb.incident_recon(),
        "otp": sb.incident_otp(),
        "payouts": sb.incident_payouts(),
    }
    sb.finish()
    (DATA / "ground_truth.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return facts


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
