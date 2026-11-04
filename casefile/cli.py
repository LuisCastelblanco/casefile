"""Run one investigation from the terminal and print the casefile as Markdown.

    python -m casefile.cli recon-spike
    python -m casefile.cli otp-delay --voice note.wav
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .agent import Investigation
from .config import RUNS_DIR, SANDBOX_DIR, model_config
from .llm import LLM
from .markdown import casefile_markdown
from .mock import MockModel
from .tools import SandboxTools


def make_llm(role: str) -> LLM:
    cfg = model_config(role)
    return LLM(cfg, mock=MockModel(role.lower()) if cfg.provider == "mock" else None)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    scenarios = {s["id"]: s for s in json.loads((SANDBOX_DIR / "scenarios.json").read_text(encoding="utf-8"))}
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=sorted(scenarios))
    ap.add_argument("--voice", help="voice note to transcribe with Muse Voice Transcribe")
    ap.add_argument("--no-screenshot", action="store_true")
    args = ap.parse_args()
    sc = scenarios[args.scenario]

    transcript = {"text": sc["voice_note_script"], "source": "typed (no recording)"}
    if args.voice:
        from .voice import transcribe
        t = transcribe(Path(args.voice).read_bytes(), sc["keywords"])
        transcript = {"text": t["text"], "source": t["model"]}
        print(f"[voice] {t['text']}\n")

    events: list[dict] = []

    def emit(ev: dict) -> None:
        ev["t"] = round(time.time(), 3)
        events.append(ev)
        kind = ev["type"]
        if kind == "model_text":
            print(f"[agent] {ev['text']}")
        elif kind == "tool_call":
            print(f"  -> {ev['name']} {json.dumps(ev['args'])[:140]}")
        elif kind == "evidence":
            print(f"     {ev['evidence']['id']}: {ev['evidence']['summary'][:120]}")
        elif kind == "verification":
            print(f"[verifier] {ev['report']['counts']} passed={ev['report']['passed']}")
        elif kind == "revision":
            print(f"[verifier] sent back {len(ev['problems'])} problem(s)")

    png = None if args.no_screenshot else (SANDBOX_DIR / "screenshots" / sc["screenshot"]).read_bytes()
    inv = Investigation(sc, make_llm("AGENT"), make_llm("VERIFIER"), make_llm("VISION"), SandboxTools(), emit)
    result = inv.run(transcript, png)
    RUNS_DIR.mkdir(exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S-cli")
    (RUNS_DIR / f"{run_id}.json").write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    print("\n" + casefile_markdown(result))
    print(f"saved run {run_id} (replay it from the UI)")


if __name__ == "__main__":
    main()
