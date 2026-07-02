# Stormx — Multi-Agent Orchestration Platform

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Stormx** is an open-source platform for creating and managing **flocks of AI agents** through a single, intuitive web UI. Design visual agent pipelines with a drag-and-drop graph editor, connect to messaging channels (Telegram, Discord, WhatsApp), equip agents with MCP tools and RAG knowledge bases, and orchestrate complex multi-agent workflows — all from your browser.

> Inspired by n8n-style visual workflows, but purpose-built for AI agent orchestration.

---

## Features

### 🧠 Multi-Agent Orchestration
- **Visual Graph Editor** — Drag, connect, and configure agents on an interactive SVG canvas
- **Three execution modes**: Sequential, Broadcast (parallel), and Looping
- **Orchestrator agent** parses user intents and delegates to specialized sub-agents
- **Checkpoint system** with time-travel debugging — rewind and restart pipelines from any step
- **Real-time SSE streaming** — see agent outputs as they happen

### 🔧 Tools & Skills
- **MCP (Model Context Protocol)** — Register external HTTP tools with API key auth
- **12 built-in skills** — Scriptwriter, Storyboarder, Voiceover, Captions, Music & Sound, Thumbnails, FFmpeg, Publishing, Translator, Revisor, SEO, Researcher
- **SkillHub integration** — Import community skills from `skills.palebluedot.live`
- **GitHub/URL imports** — Install skills directly from any repository or JSON endpoint
- **Custom Python sandbox** — Write and execute inline tools with a secure builtins environment

### 📚 RAG (Retrieval Augmented Generation)
- **ChromaDB** vector database — persistent, per-project document collections
- Upload `.txt`, `.md`, `.csv`, `.json`, `.html`, `.xml`, `.py`, `.js`, `.ts` files
- Configurable chunking (size & overlap) with semantic search
- Per-agent RAG toggle — control which agents can query your knowledge base

### 📡 Channel Integrations
- **Telegram** — Bot long-polling and webhook support, send/receive messages, trigger pipelines
- **Discord** — Read and send messages (WIP)
- **WhatsApp Cloud API** — Send messages (WIP)

### 🤖 AI Assistant
- Built-in conversational assistant that can configure your project for you
- Supports function calling to create agents, connect nodes, install skills, etc.
- Powered by OpenRouter (default: `moonshotai/kimi-k2.6:free`) — swap any OpenAI-compatible provider

### 📊 Analytics & Observability
- Real-time telementry overlay — active agents, latency, token usage
- External call log — tracks all MCP, webhook, and tool calls with timestamps
- Analytics dashboard — projects, agents, pipelines, cost breakdown

### 🔐 Security & Portability
- Per-project credential management (API keys masked in responses)
- Project export/import (JSON) for sharing and backup
- Filesystem-based persistence — no external database required

---

## Quick Start

### Prerequisites
- Python 3.12+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/tabletacunzo-cmyk/Stormx.git
cd Stormx

# Install dependencies
pip install -r requirements.txt

# Start the server
python app.py
```

Open **http://localhost:7777** in your browser.

> To change the port, set the `PORT` environment variable:
> ```bash
> $env:PORT=8080; python app.py   # Windows PowerShell
> PORT=8080 python app.py          # Linux/macOS
> ```

---

## Usage Guide

### 1. Create a Project
From the home screen, click **+ New Project** and give it a name. A default project is created automatically.

### 2. Design Your Agent Pipeline
Navigate to your project's **Graph Editor** (`#/graph/<project-name>`):

- **Add nodes**: Select node types (Orchestrator, Agent, Channel) and drag them onto the canvas
- **Connect nodes**: Click and drag from output ports to input ports
- **Configure agents**: Click an agent node to set its model, temperature, prompt, tools, and credentials
- **Edge types**: Click an edge to toggle between `optional` (default), `required`, and `loop`

### 3. Run a Pipeline
Type a brief in the input bar at the bottom of the graph editor and press Enter. Watch agents execute in real-time with streaming output.

### 4. Add Skills
Open the **Skills** panel to browse built-in skills or import from SkillHub, GitHub, or any URL. Install skills on individual agents to extend their capabilities.

### 5. Connect Channels
Configure Telegram, Discord, or WhatsApp channels to let agents communicate with users on those platforms. Channel messages can trigger pipeline execution.

### 6. Use RAG
Upload documents in the **RAG** panel. Enable RAG on specific agents to let them query your knowledge base during pipeline execution.

### 7. Save & Share
Export your project as JSON at any time. Import projects to share configurations across instances.

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Server port | `7777` |
| `PUBLIC_URL` | Public URL for webhooks (e.g. `https://example.com`) | `http://localhost:8080` |
| `TELEGRAM_BOT_TOKEN` | Default Telegram bot token | — |
| `TELEGRAM_BOT_TOKEN_<PROJECT>` | Per-project Telegram bot token (uppercase name) | — |
| `ASSISTANT_API_KEY` | API key for the built-in AI assistant | `""` (from settings) |
| `ASSISTANT_API_URL` | API endpoint for the assistant | `https://openrouter.ai/api/v1/chat/completions` |
| `ASSISTANT_MODEL` | Model name for the assistant | `moonshotai/kimi-k2.6:free` |

---

## Configuration Files

All data is stored as JSON files in the project root:

| File | Purpose |
|------|---------|
| `stormx_projects.json` | Project configurations (agents, graph, channels, etc.) |
| `stormx_settings.json` | Global settings (providers, skills, templates, MCP presets) |
| `checkpoints/` | Pipeline step checkpoints for time-travel |
| `history/` | Agent prompt history |
| `rag_data/` | ChromaDB vector store (per project) |

---

## API Overview

The backend exposes a RESTful API + SSE streaming endpoints. All routes are prefixed with `/api/`.

| Category | Endpoints |
|----------|-----------|
| **Projects** | `GET/POST /api/projects`, `DELETE /api/projects/{name}`, `GET/POST /api/projects/{name}/config` |
| **Graph** | `GET/POST /api/projects/{name}/graph` |
| **Pipeline** | `POST /api/projects/{name}/chat` (SSE stream) |
| **Agents** | `POST /api/projects/{name}/agents/{agent}/test`, `POST /api/projects/{name}/agents/{agent}/submit` |
| **Credentials** | `GET/POST /api/projects/{name}/credentials`, `DELETE /api/projects/{name}/credentials/{id}` |
| **Channels** | `GET /api/projects/{name}/channels`, `POST /api/projects/{name}/channels/{id}` |
| **MCP Tools** | `POST /api/projects/{name}/mcp/register`, `POST /api/projects/{name}/mcp/authorize` |
| **RAG** | `POST /api/projects/{name}/rag/ingest`, `POST /api/projects/{name}/rag/query` |
| **Skills** | `GET /api/skills`, `POST /api/skills/install`, `POST /api/skills/github`, `POST /api/skills/import` |
| **Assistant** | `POST /api/assistant/chat` (SSE stream) |
| **Checkpoints** | `GET/POST/DELETE /api/projects/{name}/checkpoints` |
| **Settings** | `GET/PUT /api/settings/{section}` |
| **Analytics** | `GET /api/analytics` |
| **Telegram Webhook** | `POST /api/telegram/webhook/{project}/{channel_id}` |
| **Health** | `GET /api/health` |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12, FastAPI, Uvicorn |
| **Frontend** | Vanilla JavaScript, SVG, CSS3 (dark theme) |
| **Database** | None (filesystem JSON + ChromaDB vector store) |
| **AI** | OpenAI-compatible APIs (OpenAI, Anthropic, Google, DeepSeek, OpenRouter, etc.) |
| **Messaging** | Telegram Bot API, Discord Bot API, WhatsApp Cloud API |
| **Vector Store** | ChromaDB >= 0.5.0 |
| **HTTP Client** | httpx (async) |

---

## Project Structure

```
Stormx/
├── app.py                    # FastAPI application (REST + SSE endpoints)
├── config.py                 # Project CRUD, graph validation, checkpoints, history
├── channels.py               # Telegram, Discord, WhatsApp channel integrations
├── settings.py               # Global settings (providers, skills, templates, MCP presets)
├── rag.py                    # ChromaDB RAG engine
├── requirements.txt          # Python dependencies
├── stormx_settings.json      # Global settings data
├── stormx_projects.json      # Project configurations
├── agents/
│   └── __init__.py           # Pipeline executor, MCP tools, filesystem ops, tool sandbox
├── static/
│   ├── index.html            # SPA shell
│   ├── style.css             # Dark theme stylesheet
│   └── script.js             # Full SPA frontend logic (~2300 lines)
├── checkpoints/              # Pipeline checkpoints (runtime)
├── history/                  # Agent prompt history (runtime)
└── rag_data/                 # ChromaDB persistent storage (runtime)
```

---

## Deployment

Stormx is self-contained and requires no external database.

### Production Considerations

```bash
# Set public URL for Telegram webhooks
$env:PUBLIC_URL="https://stormx.example.com"

# Configure API keys
$env:ASSISTANT_API_KEY="sk-..."
$env:TELEGRAM_BOT_TOKEN="..."

# Run with uvicorn directly for production
uvicorn app:app --host 0.0.0.0 --port 7777
```

> **Security note**: In production, ensure `stormx_projects.json` and `stormx_settings.json` are not publicly accessible. The `.gitignore` already excludes sensitive files.

---

## Built-in Agent Templates

| Agent | Color | Purpose |
|-------|-------|---------|
| Scriptwriter | Green | Video copywriting scripts |
| Storyboarder | Purple | Scene-by-scene descriptions |
| Voiceover | Amber | Voiceover narration scripts |
| Captions | Cyan | SRT subtitle generation |
| Music & Sound | Pink | Sound design recommendations |
| Thumbnails | Orange | Thumbnail design briefs |
| FFmpeg | Red | FFmpeg command generation |
| Publishing | Purple | SEO metadata & publishing |
| YouTube Assistant | Green | YouTube content management |
| Notion Assistant | Purple | Notion content annotation |

---

## Roadmap

- [ ] Full Discord bot support (send + receive + pipeline triggers)
- [ ] Full WhatsApp Cloud API (receive messages)
- [ ] Docker image and docker-compose setup
- [ ] Plugin/extension system for community contributions
- [ ] Multi-user authentication and workspace sharing
- [ ] WebSocket-based live collaboration on graphs

---

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## License

Distributed under the MIT License. See `LICENSE` for more information.

---

## Acknowledgments

- Inspired by [n8n](https://n8n.io) visual workflow design
- Built with [FastAPI](https://fastapi.tiangolo.com) and [ChromaDB](https://www.trychroma.com)
- SkillHub community at [skills.palebluedot.live](https://skills.palebluedot.live)
