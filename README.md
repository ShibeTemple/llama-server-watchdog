# llama-server watchdog

A lightweight, self-hosted dashboard for monitoring [llama.cpp](https://github.com/ggerganov/llama.cpp) server slots, health, and metrics via WebSockets.

## Features

- **Real-time slot monitoring** — sees active processing, token counts, and slot status
- **Server health & metrics** — polls `/health` and `/metrics` endpoints from llama.cpp
- **Auto-adaptive polling** — polls every 1s during active generation, 3s when idle
- **WebSocket live updates** — dashboard refreshes instantly via WebSocket, not polling
- **Zero-dependency dashboard** — single HTML file, no build step

## Quick Start

### 1. Install dependencies

```bash
pip install aiohttp
```

### 2. Configure

Copy the example `.env` and set your llama.cpp server address:

```bash
cp .env.example .env
# Edit .env with your target LLM server host and port
```

### 3. Run

```bash
python3 server.py
```

Then open your browser to `http://localhost:8124/` (or the configured listen address).

## Configuration

All settings come from environment variables or a `.env` file in the same directory.

| Variable | Default | Description |
|---|---|---|
| `LLM_STATUS_HOST` | `127.0.0.1` | llama.cpp server hostname/IP |
| `LLM_STATUS_PORT` | `8080` | llama.cpp server port |
| `LLM_STATUS_PORT_LISTEN` | `8124` | Dashboard HTTP/WebSocket port |
| `LLM_STATUS_LISTEN_ADDR` | `0.0.0.0` | Dashboard bind address |
| `LLM_STATUS_POLL` | `3` | Idle poll interval (seconds) |
| `LLM_STATUS_POLL_FAST` | `1` | Active poll interval (seconds) |

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/status` | Full status (slots, health, metrics, summary) |
| `GET /api/summary` | Compact slot summary only |
| `GET /api/slots` | Raw slots data |
| `GET /api/health` | llama.cpp health status |
| `GET /api/metrics` | llama.cpp server metrics |
| `GET /ws` | WebSocket for live updates |

## Systemd Service

To run as a persistent background service:

```bash
sudo cp llama-server-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llama-server-watchdog
```

Edit the service file to point to the correct paths and environment file.

## Files

```
.
├── server.py              # aiohttp web server + poll loop
├── dashboard.html          # Real-time dashboard (single HTML file)
├── .env                    # Local config (gitignored)
├── .env.example            # Example config
├── .gitignore              # Ignores .env and other sensitive files
├── llama-server-watchdog.service  # Systemd unit file
└── README.md               # This file
```

## License

MIT
