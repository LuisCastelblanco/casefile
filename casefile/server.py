"""FastAPI app: serves the demo UI, transcribes voice notes and streams investigations over SSE."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .agent import Investigation
from .config import ROOT, RUNS_DIR, SANDBOX_DIR, model_config
from .llm import LLM, LLMError
from .markdown import casefile_markdown
from .mock import MockModel
from .tools import SandboxTools
from .voice import VoiceError, transcribe

app = FastAPI(title="Casefile")
WEB = ROOT / "web"
SCENARIOS = {s["id"]: s for s in json.loads((SANDBOX_DIR / "scenarios.json").read_text(encoding="utf-8"))}
TOOLS = SandboxTools()
RUNS: dict[str, dict] = {}
RUNS_DIR.mkdir(exist_ok=True)


def make_llm(role: str) -> LLM:
    cfg = model_config(role)
    return LLM(cfg, mock=MockModel(role.lower()) if cfg.provider == "mock" else None)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/api/config")
def config() -> dict:
    out = {}
    for role in ("AGENT", "VERIFIER", "VISION"):
        cfg = model_config(role)
        out[role.lower()] = {"label": cfg.label, "provider": cfg.provider, "model": cfg.model,
                             "ready": cfg.provider == "mock" or bool(cfg.api_key)}
    import os
    out["voice"] = {"model": "muse-voice-transcribe-1.0", "ready": bool(os.getenv("MODEL_API_KEY"))}
    return out


@app.get("/api/scenarios")
def scenarios() -> list[dict]:
    return [{k: s[k] for k in ("id", "title", "ticket", "now", "voice_note_script", "keywords")}
            for s in SCENARIOS.values()]


@app.get("/api/scenarios/{sid}/screenshot")
def screenshot(sid: str) -> FileResponse:
    sc = SCENARIOS.get(sid) or _404()
    return FileResponse(SANDBOX_DIR / "screenshots" / sc["screenshot"], media_type="image/png")


@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile = File(...), scenario_id: str = Form("")) -> dict:
    data = await audio.read()
    keywords = SCENARIOS.get(scenario_id, {}).get("keywords", [])
    try:
        t0 = time.monotonic()
        result = await asyncio.to_thread(transcribe, data, keywords)
        result["latency_s"] = round(time.monotonic() - t0, 2)
        return result
    except VoiceError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/runs")
async def start_run(body: dict) -> dict:
    sc = SCENARIOS.get(body.get("scenario_id", "")) or _404()
    transcript = body.get("transcript") or {}
    if not transcript.get("text"):
        transcript = {"text": sc["voice_note_script"], "source": "typed (no recording)"}
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    run = {"id": run_id, "events": [], "done": False, "result": None}
    RUNS[run_id] = run

    def emit(ev: dict) -> None:
        ev["t"] = round(time.time(), 3)
        run["events"].append(ev)

    def worker() -> None:
        try:
            png = None
            if body.get("use_screenshot", True):
                png = (SANDBOX_DIR / "screenshots" / sc["screenshot"]).read_bytes()
            inv = Investigation(sc, make_llm("AGENT"), make_llm("VERIFIER"), make_llm("VISION"), TOOLS, emit)
            run["result"] = inv.run(transcript, png)
            (RUNS_DIR / f"{run_id}.json").write_text(json.dumps({"events": run["events"]}, ensure_ascii=False), encoding="utf-8")
        except (LLMError, Exception) as exc:  # surface any failure in the UI
            emit({"type": "error", "message": str(exc), "trace": traceback.format_exc()[-1500:]})
        finally:
            run["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    run = RUNS.get(run_id) or _404()

    async def gen():
        i = 0
        while True:
            while i < len(run["events"]):
                yield {"data": json.dumps(run["events"][i], ensure_ascii=False)}
                i += 1
            if run["done"] and i >= len(run["events"]):
                yield {"event": "end", "data": "{}"}
                return
            await asyncio.sleep(0.15)

    return EventSourceResponse(gen())


@app.get("/api/saved")
def saved() -> list[dict]:
    out = []
    for f in sorted(RUNS_DIR.glob("*.json"), reverse=True)[:30]:
        try:
            events = json.loads(f.read_text(encoding="utf-8")).get("events", [])
        except (OSError, ValueError):
            continue  # empty or partially written run file
        final = next((e for e in events if e["type"] == "final"), None)
        if final:
            r = final["result"]
            out.append({"id": f.stem, "scenario": r["scenario"], "title": r["casefile"].get("title", ""),
                        "agent": r["models"]["agent"]})
    return out


@app.get("/api/saved/{run_id}")
def saved_run(run_id: str) -> JSONResponse:
    path = RUNS_DIR / f"{Path(run_id).name}.json"
    if not path.exists():
        _404()
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/runs/{run_id}/markdown", response_class=PlainTextResponse)
def run_markdown(run_id: str) -> str:
    run = RUNS.get(run_id)
    result = run["result"] if run else None
    if result is None:
        path = RUNS_DIR / f"{Path(run_id).name}.json"
        if path.exists():
            final = next((e for e in json.loads(path.read_text(encoding="utf-8"))["events"] if e["type"] == "final"), None)
            result = final["result"] if final else None
    if result is None:
        _404()
    return casefile_markdown(result)


def _404():
    raise HTTPException(404, "not found")
