"""Render the synthetic screenshots attached to each incident ticket.

They are drawn from the sandbox data so every number on screen is consistent
with what the agent can later query. Run after build_sandbox.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "screenshots"
FONT_DIR = Path("/usr/share/fonts/opentype/inter")


def font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    for name in (f"Inter-{weight}.otf", "Inter-Regular.otf"):
        p = FONT_DIR / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


BG, PANEL, LINE, TEXT, MUTED = "#0f1419", "#171e26", "#263241", "#e6edf3", "#8b98a8"
RED, AMBER, GREEN, BLUE = "#f0564a", "#e9a23b", "#3fb68b", "#5b9cf2"


def chrome(d: ImageDraw.ImageDraw, w: int, title: str, url: str) -> None:
    d.rectangle([0, 0, w, 44], fill="#0a0e13")
    for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        d.ellipse([16 + i * 20, 16, 28 + i * 20, 28], fill=c)
    d.rounded_rectangle([90, 10, w - 20, 34], 6, fill="#1b232d")
    d.text((104, 14), url, fill=MUTED, font=font(13))
    d.text((24, 60), title, fill=TEXT, font=font(22, "SemiBold"))


def recon(db: sqlite3.Connection) -> None:
    w, h = 1200, 720
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    chrome(d, w, "Settlement Reconciliation — Daily runs", "https://ops.kestrelpay.internal/recon/runs")
    runs = db.execute("SELECT run_date, matched, exceptions, status, ledger_count FROM recon_runs ORDER BY run_date").fetchall()
    last = runs[-1]
    # KPI cards
    cards = [("Run date", last[0], TEXT), ("Status", last[3], RED), ("Exceptions", f"{last[2]:,}", RED),
             ("Threshold", "25", MUTED), ("Matched", f"{last[1]:,} / {last[4]:,}", TEXT)]
    x = 24
    for label, val, col in cards:
        d.rounded_rectangle([x, 104, x + 214, 194], 10, fill=PANEL, outline=LINE)
        d.text((x + 16, 118), label.upper(), fill=MUTED, font=font(12, "Medium"))
        d.text((x + 16, 142), val, fill=col, font=font(26 if len(val) < 14 else 16, "SemiBold"))
        x += 230
    # bar chart
    d.rounded_rectangle([24, 214, 700, 690], 10, fill=PANEL, outline=LINE)
    d.text((44, 232), "Exceptions per run", fill=TEXT, font=font(16, "SemiBold"))
    base, top = 640, 290
    mx = max(r[2] for r in runs)
    bw = 90
    for i, r in enumerate(runs):
        bx = 90 + i * 150
        bh = max(3, (base - top) * r[2] / mx)
        d.rectangle([bx, base - bh, bx + bw, base], fill=RED if r[2] > 25 else BLUE)
        d.text((bx + 10, base + 12), r[0][5:], fill=MUTED, font=font(13))
        d.text((bx + 10, base - bh - 24), str(r[2]), fill=TEXT, font=font(14, "Medium"))
    thr_y = base - (base - top) * 25 / mx
    d.line([70, thr_y, 680, thr_y], fill=AMBER, width=2)
    d.text((560, thr_y - 20), "threshold 25", fill=AMBER, font=font(12))
    # reason table
    d.rounded_rectangle([720, 214, 1176, 690], 10, fill=PANEL, outline=LINE)
    d.text((740, 232), f"Exceptions by reason — {last[0]}", fill=TEXT, font=font(16, "SemiBold"))
    reasons = db.execute("SELECT reason, COUNT(*) FROM recon_exceptions WHERE run_date=? GROUP BY reason",
                         (last[0],)).fetchall()
    y = 276
    for reason, cnt in reasons:
        d.text((740, y), reason, fill=TEXT, font=font(14, "Medium"))
        d.text((1100, y), str(cnt), fill=RED, font=font(14, "SemiBold"))
        y += 32
    d.text((740, y + 10), "Sample", fill=MUTED, font=font(12, "Medium"))
    y += 36
    sample = db.execute("SELECT txn_ref, ledger_business_date, settlement_business_date FROM recon_exceptions"
                        " WHERE run_date=? LIMIT 8", (last[0],)).fetchall()
    for ref, lbd, sbd in sample:
        d.text((740, y), f"{ref}   ledger {lbd}   settle {sbd}", fill=MUTED, font=font(13))
        y += 26
    d.text((740, 650), "Processor file PRC-SETTLE-20261031.csv arrived 06:14 (SLA 06:00)", fill=AMBER,
           font=font(12))
    im.save(OUT / "recon.png")


def otp() -> None:
    metrics = json.loads((DATA / "metrics.json").read_text(encoding="utf-8"))
    lag = metrics["notification-dispatcher"]["dispatch_lag_p95_seconds"]
    prov = metrics["sms-gateway"]["provider_latency_p95_ms"]
    w, h = 1200, 720
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    chrome(d, w, "Messaging — OTP delivery", "https://grafana.kestrelpay.internal/d/otp-delivery")
    d.text((24, 92), "2026-11-02  07:00 → 10:30 (UTC-5)", fill=MUTED, font=font(13))

    def panel(x0, y0, x1, y1, title, pts, unit, color, ymax=None):
        d.rounded_rectangle([x0, y0, x1, y1], 10, fill=PANEL, outline=LINE)
        d.text((x0 + 18, y0 + 14), title, fill=TEXT, font=font(15, "SemiBold"))
        vals = [p[1] for p in pts]
        top = ymax or max(vals) * 1.1
        px0, py0, px1, py1 = x0 + 60, y0 + 50, x1 - 20, y1 - 40
        for k in range(5):
            gy = py1 - (py1 - py0) * k / 4
            d.line([px0, gy, px1, gy], fill=LINE)
            d.text((x0 + 12, gy - 8), f"{top * k / 4:.0f}", fill=MUTED, font=font(11))
        n = len(vals)
        coords = [(px0 + (px1 - px0) * i / (n - 1), py1 - (py1 - py0) * min(v, top) / top) for i, v in enumerate(vals)]
        d.line(coords, fill=color, width=3)
        for i in range(0, n, 6):
            d.text((coords[i][0] - 14, py1 + 10), pts[i][0][11:16], fill=MUTED, font=font(11))
        d.text((x1 - 80, y0 + 16), unit, fill=MUTED, font=font(12))

    panel(24, 120, 1176, 430, "Dispatch lag p95 (enqueue → dispatch)", lag, "seconds", RED)
    panel(24, 450, 1176, 700, "SMS provider latency p95 (Altavoz)", prov, "ms", GREEN, ymax=3000)
    d.rounded_rectangle([880, 140, 1160, 190], 8, fill="#3a1d1d", outline=RED)
    d.text((896, 156), "ALERT otp.dispatch.lag  FIRING", fill=RED, font=font(14, "SemiBold"))
    im.save(OUT / "otp.png")


def payouts(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT p.merchant_id, m.name, p.payout_id, p.amount_cop FROM ledger_postings lp JOIN payouts p"
        " ON p.payout_id = lp.payout_id JOIN merchants m ON m.merchant_id = p.merchant_id"
        " WHERE p.batch_id='B-20261102' GROUP BY lp.payout_id HAVING COUNT(*) > 1 ORDER BY p.payout_id LIMIT 1"
    ).fetchone()
    postings = db.execute("SELECT posting_id, posted_at FROM ledger_postings WHERE payout_id=? ORDER BY posted_at",
                          (row[2],)).fetchall()
    w, h = 1200, 720
    im = Image.new("RGB", (w, h), "#f5f6f8")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, 44], fill="#e4e7ec")
    for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        d.ellipse([16 + i * 20, 16, 28 + i * 20, 28], fill=c)
    d.rounded_rectangle([90, 10, w - 20, 34], 6, fill="#ffffff")
    d.text((104, 14), "https://merchants.kestrelpay.com/account/movements", fill="#6b7280", font=font(13))
    d.rectangle([0, 44, w, 110], fill="#13294b")
    d.text((32, 64), "Kestrel Pay  ·  Merchant portal", fill="white", font=font(20, "SemiBold"))
    d.text((860, 70), f"{row[1]}  ({row[0]})", fill="#c7d2e3", font=font(15))
    d.text((32, 136), "Account movements", fill="#111827", font=font(26, "SemiBold"))
    d.text((32, 176), "Settlement deposits — last 3 days", fill="#6b7280", font=font(14))
    hdr = ["Date / time", "Description", "Reference", "Amount (COP)"]
    xs = [32, 300, 640, 960]
    d.rectangle([24, 210, 1176, 250], fill="#e9edf3")
    for x, t in zip(xs, hdr):
        d.text((x, 222), t, fill="#374151", font=font(14, "SemiBold"))
    y = 262
    lines = [(pst[1].replace("T", " ")[:19], "Settlement deposit B-20261102", pst[0], row[3], True) for pst in postings]
    prev = db.execute("SELECT lp.posted_at, lp.posting_id, lp.amount_cop FROM ledger_postings lp JOIN payouts p ON"
                      " p.payout_id=lp.payout_id WHERE p.merchant_id=? AND p.batch_id='B-20261101'",
                      (row[0],)).fetchone()
    lines.append((prev[0].replace("T", " ")[:19], "Settlement deposit B-20261101", prev[1], prev[2], False))
    for when, desc, ref, amt, hi in lines:
        if hi:
            d.rectangle([24, y - 8, 1176, y + 30], fill="#fff4e5")
        d.text((xs[0], y), when, fill="#111827", font=font(15))
        d.text((xs[1], y), desc, fill="#111827", font=font(15))
        d.text((xs[2], y), ref, fill="#4b5563", font=font(15))
        d.text((xs[3], y), f"+ {amt:,.0f}".replace(",", "."), fill="#047857", font=font(15, "SemiBold"))
        y += 48
    d.rounded_rectangle([24, 520, 1176, 680], 10, fill="#ffffff", outline="#d1d5db")
    d.text((44, 540), "Support chat — SUP-88213", fill="#111827", font=font(16, "SemiBold"))
    d.text((44, 576), "Merchant: \"We received the 2 November settlement twice, same amount. Is this an error?",
           fill="#374151", font=font(15))
    d.text((44, 602), "We don't want to spend money that will be taken back.\"", fill="#374151", font=font(15))
    d.text((44, 640), "Agent: escalated to L2 payments 08:20", fill="#6b7280", font=font(13))
    im.save(OUT / "payouts.png")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    db = sqlite3.connect(DATA / "kestrel.db")
    recon(db)
    otp()
    payouts(db)
    print("screenshots written to", OUT)


if __name__ == "__main__":
    main()
