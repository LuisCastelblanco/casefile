"""Check every Meta model integration before a real run.

    python scripts/smoke_test.py                  # chat, tool calling, vision
    python scripts/smoke_test.py --voice note.wav # + Muse Voice Transcribe
    python scripts/smoke_test.py --baseten        # + Muse Spark Open on Baseten

Prints PASS/FAIL per check with the raw error, so a wrong field name or model
id is obvious in seconds instead of in the middle of the demo.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from casefile.config import PROVIDERS, ModelConfig, model_config  # noqa: E402
from casefile.llm import LLM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOOL = [{"type": "function", "function": {"name": "get_exceptions", "description": "Count recon exceptions for a date",
         "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}}}]


def check(name, fn):
    try:
        detail = fn()
        print(f"PASS  {name}: {detail}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
        return False


def run_checks(llm: LLM) -> list[bool]:
    out = []
    out.append(check(f"chat [{llm.label}]", lambda: llm.chat([{"role": "user", "content": "Reply with the word ready."}],
                                                             max_tokens=2000).content.strip()[:60]))

    def tools():
        msgs = [{"role": "user", "content": "How many reconciliation exceptions were there on 2026-10-31? Use the tool."}]
        r = llm.chat(msgs, tools=TOOL)
        assert r.tool_calls, f"no tool call, got text: {r.content[:120]}"
        call = r.tool_calls[0]
        msgs += [r.message, {"role": "tool", "tool_call_id": call.id, "content": json.dumps({"exceptions": 635})}]
        r2 = llm.chat(msgs, tools=TOOL)
        assert "635" in r2.content, f"final answer missing 635: {r2.content[:120]}"
        return f"{call.name}({call.arguments}) -> {r2.content.strip()[:60]}"

    out.append(check(f"tool calling round trip [{llm.label}]", tools))

    def vision():
        png = (ROOT / "sandbox" / "screenshots" / "recon.png").read_bytes()
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        r = llm.chat([{"role": "user", "content": [
            {"type": "text", "text": "What number is shown under EXCEPTIONS? Reply with the number only."},
            {"type": "image_url", "image_url": {"url": url}}]}], max_tokens=2000)
        return r.content.strip()[:60]

    out.append(check(f"image understanding [{llm.label}]", vision))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", help="16 kHz mono WAV to transcribe (any format if ffmpeg is installed)")
    ap.add_argument("--baseten", action="store_true", help="also test Muse Spark Open on Baseten")
    args = ap.parse_args()

    results = run_checks(LLM(model_config("AGENT")))
    if args.baseten:
        import os
        p = PROVIDERS["baseten"]
        cfg = ModelConfig("baseten", os.getenv("BASETEN_MODEL", p["default_model"]), p["base_url"],
                          os.getenv(p["key_env"], ""), None)
        results += run_checks(LLM(cfg))
    if args.voice:
        from casefile.voice import transcribe
        results.append(check("Muse Voice Transcribe", lambda: transcribe(Path(args.voice).read_bytes(),
                                                                          ["reconciliation", "OTP"])["text"][:120]))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
