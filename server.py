#!/usr/bin/env python3
"""
LLM Server Status Dashboard - aiohttp based
Monitors llama.cpp server via /health, /slots, /metrics endpoints
"""

import asyncio
import json
import time
import logging
import sys
import os
from datetime import datetime, timezone
from aiohttp import web, WSMsgType

# ── Load .env file (if present) ────────────────────────────
# Reads environment variables from a .env file in the same directory.
# Each line: KEY=value (comments and blank lines are skipped).
# This allows per-install overrides without modifying source code.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_SCRIPT_DIR, ".env")
if os.path.isfile(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip surrounding quotes if present
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                os.environ.setdefault(key, value)

# ── Configuration ──────────────────────────────────────────
LLM_HOST = os.environ.get("LLM_STATUS_HOST", "127.0.0.1")
LLM_PORT = int(os.environ.get("LLM_STATUS_PORT", "8080"))
LLM_BASE = f"http://{LLM_HOST}:{LLM_PORT}"
POLL_INTERVAL = int(os.environ.get("LLM_STATUS_POLL", "3"))
LISTEN_ADDR = os.environ.get("LLM_STATUS_LISTEN_ADDR", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LLM_STATUS_PORT_LISTEN", "8124"))
POLL_FAST = int(os.environ.get("LLM_STATUS_POLL_FAST", "1"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("llmstatus")

# ── Global State ───────────────────────────────────────────────
state = {
    "slots": [],
    "health": {},
    "metrics": {},
    "last_update": None,
    "update_errors": 0,
    "uptime": time.time(),
    "poll_count": 0,
}

# Connected WebSocket clients
ws_clients = set()


async def poll_llm_server():
    """Fetch all data from llama-server and update global state."""
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        tasks = {}
        tasks["health"] = session.get(f"{LLM_BASE}/health")
        tasks["slots"] = session.get(f"{LLM_BASE}/slots")
        # /metrics is optional — llama.cpp may not have it
        tasks["metrics"] = session.get(f"{LLM_BASE}/metrics")
        
        results = {}
        errors = 0
        # Only health and slots are required endpoints
        required = {"health", "slots"}
        
        for key, coro in tasks.items():
            try:
                async with coro as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if key == "slots":
                            try:
                                results[key] = json.loads(text)
                            except json.JSONDecodeError:
                                results[key] = text
                        else:
                            try:
                                results[key] = json.loads(text)
                            except json.JSONDecodeError:
                                # Prometheus text or other non-JSON format — store raw
                                results[key] = text
                    else:
                        results[key] = {"error": f"HTTP {resp.status}"}
                        errors += (1 if key in required else 0)
            except Exception as e:
                results[key] = {"error": str(e)}
                errors += (1 if key in required else 0)
        
        # Update global state
        state["slots"] = results.get("slots", [])
        state["health"] = results.get("health", {})
        state["metrics"] = results.get("metrics", {})
        state["last_update"] = datetime.now(timezone.utc).isoformat()
        state["update_errors"] = errors
        state["poll_count"] += 1

    if errors > 0:
        log.warning(f"Poll errors: {errors}/3 endpoints failed")


def compute_slot_summary(slots):
    """Compute a summary of all slots for compact display."""
    total = len(slots) if isinstance(slots, list) else 0
    busy = 0
    processing = 0
    active_tokens = 0
    prompt_tokens = 0
    generation_tokens = 0
    
    if isinstance(slots, list):
        for slot in slots:
            if isinstance(slot, dict):
                if slot.get("is_processing", False):
                    processing += 1
                if slot.get("is_running", False) or slot.get("is_busy", False):
                    busy += 1
                # llama.cpp slots use next_token[0].n_decoded for decoded token count
                nt = slot.get("next_token", [])
                if isinstance(nt, list) and len(nt) > 0:
                    active_tokens += nt[0].get("n_decoded", 0)
                # Derive prompt tokens: total context used minus generation tokens
                # n_decoded = tokens generated so far; n_ctx = context window
                # We can't know exact prompt count, but n_decoded gives us gen tokens
                generation_tokens += nt[0].get("n_decoded", 0) if isinstance(nt, list) and len(nt) > 0 else 0
    
    return {
        "total": total,
        "busy": busy,
        "processing": processing,
        "active_tokens": active_tokens,
        "total_prompt_tokens": prompt_tokens,
        "total_generation_tokens": generation_tokens,
        "active_slots": processing,
    }


def build_status_response():
    """Build the full status response."""
    # Determine current poll interval
    any_active = False
    for slot in state.get("slots", []):
        if isinstance(slot, dict):
            if slot.get("is_processing") or slot.get("is_running") or slot.get("is_busy"):
                any_active = True
                break
    current_poll = POLL_FAST if any_active else POLL_INTERVAL
    
    summary = compute_slot_summary(state["slots"])
    return {
        "status": "ok" if state["update_errors"] == 0 else "degraded",
        "error_count": state["update_errors"],
        "summary": summary,
        "slots": state["slots"],
        "health": state["health"],
        "metrics": state["metrics"],
        "last_update": state["last_update"],
        "poll_count": state["poll_count"],
        "server_uptime": time.time() - state["uptime"],
        "llm_endpoint": LLM_BASE,
        "current_poll_interval": current_poll,
    }


# ── WebSocket Handler ──────────────────────────────────────────
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ws_clients.add(ws)
    log.info(f"WebSocket client connected. Total clients: {len(ws_clients)}")
    
    try:
        # Send initial state immediately
        await ws.send_json(build_status_response())
        
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                if msg.data == "ping":
                    await ws.send_json({"type": "pong", "time": datetime.now(timezone.utc).isoformat()})
            elif msg.type == WSMsgType.ERROR:
                log.warning(f"WebSocket error: {ws.exception()}")
    except Exception as e:
        log.error(f"WebSocket handler error: {e}")
    finally:
        ws_clients.discard(ws)
        log.info(f"WebSocket client disconnected. Total clients: {len(ws_clients)}")
    
    return ws


# ── Broadcast helper ───────────────────────────────────────────
async def broadcast(status_data):
    """Send status update to all connected WebSocket clients."""
    global ws_clients
    if not ws_clients:
        return
    msg = json.dumps(status_data)
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_str(msg)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ── HTTP Routes ────────────────────────────────────────────────
async def api_status(request):
    """GET /api/status - Full status endpoint."""
    return web.json_response(build_status_response())


async def api_summary(request):
    """GET /api/summary - Compact summary only."""
    return web.json_response({
        "status": "ok" if state["update_errors"] == 0 else "degraded",
        "summary": compute_slot_summary(state["slots"]),
        "last_update": state["last_update"],
        "error_count": state["update_errors"],
    })


async def api_slots(request):
    """GET /api/slots - Raw slots data."""
    return web.json_response(state["slots"])


async def api_health(request):
    """GET /api/health - Health check."""
    return web.json_response(state["health"])


async def api_metrics(request):
    """GET /api/metrics - Server metrics."""
    return web.json_response(state["metrics"])


async def dashboard(request):
    """GET / - Main dashboard HTML."""
    return web.FileResponse("/root/llm_status_server/dashboard.html")


# ── Polling Loop ───────────────────────────────────────────────
async def poll_loop():
    """Main polling loop - fetches data and broadcasts updates.
    Polls at 1s when any slot is active, 3s when all idle."""
    slow_poll = POLL_INTERVAL
    fast_poll = POLL_FAST
    log.info(f"Polling llama-server at {LLM_BASE} (fast={fast_poll}s, slow={slow_poll}s)")
    while True:
        try:
            await poll_llm_server()
            # Check if any slot is active to decide next interval
            any_active = False
            for slot in state.get("slots", []):
                if isinstance(slot, dict):
                    if slot.get("is_processing") or slot.get("is_running") or slot.get("is_busy"):
                        any_active = True
                        break
            poll_interval = fast_poll if any_active else slow_poll
            status_data = build_status_response()
            await broadcast(status_data)
        except Exception as e:
            log.error(f"Poll loop error: {e}")
        await asyncio.sleep(poll_interval)


# ── Application Setup ──────────────────────────────────────────
def create_app():
    app = web.Application()
    
    # API routes
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/summary", api_summary)
    app.router.add_get("/api/slots", api_slots)
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/metrics", api_metrics)
    
    # WebSocket
    app.router.add_get("/ws", websocket_handler)
    
    # Dashboard
    app.router.add_get("/", dashboard)
    
    return app


# ── Entry Point ────────────────────────────────────────────────
if __name__ == "__main__":
    app = create_app()
    
    # Start the polling loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    poll_task = loop.create_task(poll_loop())
    
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, LISTEN_ADDR, LISTEN_PORT)
    loop.run_until_complete(site.start())
    
    log.info(f"LLM Status Server running on http://{LISTEN_ADDR}:{LISTEN_PORT}")
    log.info(f"Dashboard: http://{LISTEN_ADDR}:{LISTEN_PORT}/")
    log.info(f"API: http://{LISTEN_ADDR}:{LISTEN_PORT}/api/status")
    log.info(f"WebSocket: ws://{LISTEN_ADDR}:{LISTEN_PORT}/ws")
    log.info(f"Target LLM endpoint: {LLM_BASE}")
    
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        poll_task.cancel()
        loop.run_until_complete(runner.cleanup())
        loop.close()
