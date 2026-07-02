import json, os, asyncio, time, uvicorn, httpx, re, hashlib, threading, logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from config import (
    list_projects, get_project, create_project, delete_project,
    rename_project, save_project_config, reset_project_config,
    save_graph, get_graph, save_checkpoint, get_checkpoints,
    delete_checkpoints, restore_from_checkpoint,
    get_history, append_to_history, clear_history,
    is_valid_project_name,
)
from agents import run_pipeline, run_pipeline_graph, execute_tool, call_mcp_tool, register_mcp_tool, call_model_stream as _agent_stream, MCP_REGISTRY
from rag import ingest as rag_ingest, query as rag_query, list_documents as rag_list, delete_document as rag_delete
from channels import registry as channel_registry
from channels import TelegramChannel, log_incoming, INCOMING_LOG
from channels import DiscordChannel, WhatsAppChannel
import settings as stormx_settings

from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("stormx")

async def _read_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None

# Per-project per-chat conversation history: {project: {chat_id: [msg, ...]}}
_CHAT_HISTORY: dict = {}

def _get_chat_history(project: str, chat_id: str) -> list:
    return _CHAT_HISTORY.setdefault(project, {}).setdefault(chat_id, [])

async def _handle_channel_trigger(project: str, channel_node_id: str, chat_id: str, text: str):
    """Run pipeline triggered by a channel message."""
    cfg = get_project(project)
    if not cfg:
        return
    ork = dict(cfg.get("orchestrator", {}))
    ork["_project"] = project
    agents_cfg = cfg.get("agents", {})
    all_channels_cfg = cfg.get("channels", {})
    graph = cfg.get("graph", {"nodes": [], "edges": []})
    graph_node_ids = {n["id"] for n in graph.get("nodes", [])}
    channels_cfg = {k: v for k, v in all_channels_cfg.items() if k in graph_node_ids}
    brief = text
    history = _get_chat_history(project, chat_id)
    history.append({"role": "user", "content": text})
    from agents import run_pipeline_graph as _run
    last = None
    async for chunk in _run(brief, graph, ork, agents_cfg, channels_cfg,
                            channel_trigger={"text": text, "chat_id": chat_id, "channel_node_id": channel_node_id},
                            history=history):
        last = chunk
    if last:
        try:
            parsed = json.loads(last)
            if parsed.get("final") and parsed.get("type") == "agent_done":
                history.append({"role": "assistant", "content": parsed.get("content", "")})
        except Exception:
            pass
    if len(history) > 20:
        history[:] = history[-20:]

_POLLING_TASKS = []

async def _poll_telegram_channels():
    """Long-poll Telegram: ONE poller per unique bot token (avoids 409 conflicts).

    A single bot token may be referenced by multiple project/channel nodes; the
    poller dispatches each inbound message to all of them.
    """
    poll_tasks: dict[str, asyncio.Task] = {}
    token_targets: dict[str, list] = {}

    async def _poll_single(bot_token: str, targets: list):
        tg = channel_registry.register_telegram(bot_token)
        conflict_logged = False
        while True:
            try:
                updates = await tg.get_updates(timeout=30)
                conflict_logged = False
                for upd in updates:
                    msg = upd.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", msg.get("caption", ""))
                    sender = msg.get("from", {}).get("first_name", "") or msg.get("from", {}).get("username", "")
                    if not (chat_id and text):
                        continue
                    logger.info("Telegram inbound %s: %s", chat_id, text[:80])
                    for (project, ch_id) in targets:
                        log_incoming("telegram", project, ch_id, text=text, sender=sender,
                                     payload=json.dumps(upd)[:300], status=200)
                        asyncio.create_task(_handle_channel_trigger(project, ch_id, chat_id, text))
            except asyncio.CancelledError:
                break
            except Exception as e:
                msg = str(e)
                if msg.startswith("409"):
                    if not conflict_logged:
                        for (project, ch_id) in targets:
                            log_incoming("telegram", project, ch_id, text="[poll conflict — clearing webhook]",
                                         payload=msg[:300], status=409)
                        try:
                            await tg.delete_webhook()
                        except Exception:
                            pass
                        conflict_logged = True
                    await asyncio.sleep(2)
                else:
                    logger.debug("Poll error %s: %s", bot_token[:10], msg)
                    await asyncio.sleep(1)

    try:
        while True:
            wanted: dict[str, list] = {}
            for p in list_projects():
                pname = p["name"]
                cfg = get_project(pname)
                if not cfg:
                    continue
                for ch_id, ch in cfg.get("channels", {}).items():
                    if ch.get("type") != "telegram" or not ch.get("enabled", True):
                        continue
                    token = channel_registry.resolve_bot_token(pname, ch.get("config", {}))
                    if not token:
                        continue
                    wanted.setdefault(token, []).append((pname, ch_id))
            token_targets = wanted
            for token, targets in wanted.items():
                if token not in poll_tasks or poll_tasks[token].done():
                    poll_tasks[token] = asyncio.create_task(_poll_single(token, targets))
            stale = [t for t in poll_tasks if t not in wanted]
            for t in stale:
                poll_tasks[t].cancel()
                poll_tasks.pop(t, None)
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        for t in poll_tasks.values():
            t.cancel()
        raise


@asynccontextmanager
async def _lifespan(app):
    logger.info("Starting Stormo AI Orchestrator")
    public_url = os.environ.get("PUBLIC_URL", "")
    for p in list_projects():
        cfg = get_project(p["name"])
        if not cfg:
            continue
        for t in cfg.get("mcp_tools", []):
            if t.get("name") and t.get("url"):
                register_mcp_tool(t["name"], t["url"], t.get("description", ""), api_key=t.get("api_key", ""))
        channels = cfg.get("channels", {})
        for ch_id, ch in channels.items():
            if ch.get("type") == "telegram" and ch.get("enabled", True):
                token = channel_registry.resolve_bot_token(p["name"], ch.get("config", {}))
                if token:
                    tg = channel_registry.register_telegram(token, p["name"], ch_id)
                    if public_url:
                        webhook_url = f"{public_url.rstrip('/')}/api/telegram/webhook/{p['name']}/{ch_id}"
                        asyncio.create_task(tg.set_webhook(webhook_url))
    task = asyncio.create_task(_poll_telegram_channels())
    _POLLING_TASKS.append(task)
    yield
    for t in _POLLING_TASKS:
        t.cancel()
    logger.info("Shutting down Stormo AI Orchestrator")

app = FastAPI(title="Stormo AI Orchestrator", docs_url=None, redoc_url=None, lifespan=_lifespan)

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.method} {request.url.path}")
    return JSONResponse({"status": "error", "error": "Internal server error"}, status_code=500)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
RAG_DIR = os.path.join(os.path.dirname(__file__), "rag_data")
# OBS_FILE = os.path.join(os.path.dirname(__file__), "observability.json")

_asst_settings = stormx_settings.get_env_overrides()

def _get_asst_setting(key, default=""):
    return _asst_settings.get(key, default)

BUILTIN_SKILLS = stormx_settings.get_section("skills")
AGENT_TEMPLATES = stormx_settings.get_section("templates")
PROVIDERS = stormx_settings.get_section("providers")

# ===== HELPERS =====
def _resolve_cred(agent_cfg, project_cfg):
    api_key = agent_cfg.get("api_key", "")
    api_url = agent_cfg.get("api_url", "")
    cred_id = agent_cfg.get("credential_id", "")
    if not api_key and cred_id:
        creds = project_cfg.get("credentials", {})
        cred = creds.get(cred_id, {})
        api_key = cred.get("api_key", "")
        api_url = cred.get("api_url", "")
    return {"api_key": api_key, "api_url": api_url}

async def call_model_stream(api_key, api_url, model, system_prompt, user_message, temperature=0.3):
    async for chunk in _agent_stream(api_key, api_url, model, system_prompt, user_message, temperature):
        yield chunk

# ===== SETTINGS API =====
@app.get("/api/settings")
async def api_settings_get():
    return stormx_settings.get_all()

@app.get("/api/settings/{section}")
async def api_settings_section(section: str):
    valid = ("providers", "skills", "templates", "mcp_presets")
    if section not in valid:
        return JSONResponse({"error": "Invalid section"}, status_code=400)
    return stormx_settings.get_section(section)

@app.put("/api/settings/{section}")
async def api_settings_update(section: str, request: Request):
    valid = ("providers", "skills", "templates", "mcp_presets")
    if section not in valid:
        return JSONResponse({"error": "Invalid section"}, status_code=400)
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    stormx_settings.update_section(section, body)
    global BUILTIN_SKILLS, AGENT_TEMPLATES, PROVIDERS
    if section == "skills":
        BUILTIN_SKILLS = stormx_settings.get_section("skills")
    elif section == "templates":
        AGENT_TEMPLATES = stormx_settings.get_section("templates")
    elif section == "providers":
        PROVIDERS = stormx_settings.get_section("providers")
    return {"status": "ok"}

@app.post("/api/settings/{section}")
async def api_settings_add(section: str, request: Request):
    valid = ("providers", "skills", "templates", "mcp_presets")
    if section not in valid:
        return JSONResponse({"error": "Invalid section"}, status_code=400)
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    key_field = "key" if section == "providers" else "name" if section == "mcp_presets" else "id"
    stormx_settings.add_item(section, body, key_field)
    global BUILTIN_SKILLS, AGENT_TEMPLATES, PROVIDERS
    if section == "skills":
        BUILTIN_SKILLS = stormx_settings.get_section("skills")
    elif section == "templates":
        AGENT_TEMPLATES = stormx_settings.get_section("templates")
    elif section == "providers":
        PROVIDERS = stormx_settings.get_section("providers")
    return {"status": "ok"}

@app.delete("/api/settings/{section}/{item_key}")
async def api_settings_delete(section: str, item_key: str):
    valid = ("providers", "skills", "templates", "mcp_presets")
    if section not in valid:
        return JSONResponse({"error": "Invalid section"}, status_code=400)
    key_field = "key" if section == "providers" else "name" if section == "mcp_presets" else "id"
    stormx_settings.remove_item(section, item_key, key_field)
    global BUILTIN_SKILLS, AGENT_TEMPLATES, PROVIDERS
    if section == "skills":
        BUILTIN_SKILLS = stormx_settings.get_section("skills")
    elif section == "templates":
        AGENT_TEMPLATES = stormx_settings.get_section("templates")
    elif section == "providers":
        PROVIDERS = stormx_settings.get_section("providers")
    return {"status": "ok"}

# ===== PROJECTS CRUD =====
@app.get("/api/projects")
async def api_list_projects():
    return list_projects()

@app.post("/api/projects")
async def api_create_project(request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"status": "error", "error": "JSON body required"}, status_code=400)
    name = str(body.get("name", "")).strip()
    if not is_valid_project_name(name):
        return JSONResponse({"status": "error", "error": "Invalid project name (1-64 chars: letters, numbers, -, _)"}, status_code=400)
    if not create_project(name):
        return JSONResponse({"status": "error", "error": "Project already exists or invalid name"}, status_code=409)
    return {"status": "ok", "project": get_project(name)}

@app.delete("/api/projects/{name}")
async def api_delete_project(name: str):
    if not is_valid_project_name(name):
        return JSONResponse({"status": "error", "error": "Invalid project name"}, status_code=400)
    if delete_project(name):
        return {"status": "ok"}
    return JSONResponse({"status": "error", "error": "Unable to delete"}, status_code=400)

@app.put("/api/projects/{name}")
async def api_rename_project(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"status": "error", "error": "JSON body required"}, status_code=400)
    new_name = str(body.get("name", "")).strip()
    if not is_valid_project_name(new_name):
        return JSONResponse({"status": "error", "error": "Invalid project name"}, status_code=400)
    if rename_project(name, new_name):
        return {"status": "ok", "name": new_name}
    return JSONResponse({"status": "error", "error": "Unable to rename"}, status_code=400)

@app.get("/api/projects/{name}/config")
async def api_get_project(name: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return cfg

@app.post("/api/projects/{name}/config")
async def api_save_config(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"status": "error", "error": "JSON body required"}, status_code=400)
    if save_project_config(name, body):
        return {"status": "ok"}
    return JSONResponse({"status": "error", "error": "Project not found"}, status_code=404)

@app.post("/api/projects/{name}/config/reset")
async def api_reset_config(name: str):
    cfg = reset_project_config(name)
    if cfg:
        return cfg
    return JSONResponse({"error": "Project not found"}, status_code=404)

# ===== GRAPH =====
@app.post("/api/projects/{name}/graph")
async def api_save_graph(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"status": "error", "error": "JSON body required"}, status_code=400)
    if save_graph(name, body):
        return {"status": "ok"}
    return JSONResponse({"status": "error", "error": "Project not found"}, status_code=404)

@app.get("/api/projects/{name}/graph")
async def api_get_graph(name: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return cfg.get("graph", {"nodes": [], "edges": []})

# ===== PIPELINE =====
@app.post("/api/projects/{name}/chat")
async def api_chat(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    brief = body.get("brief", "")
    history = body.get("history", [])
    if not brief.strip(): return JSONResponse({"error": "Empty brief"}, status_code=400)
    cfg = get_project(name)
    if not cfg: return JSONResponse({"error": "Project not found"}, status_code=404)
    ork = dict(cfg.get("orchestrator", {}))
    ork["_project"] = name
    agents_cfg = cfg.get("agents", {})
    all_channels_cfg = cfg.get("channels", {})
    graph = cfg.get("graph", {"nodes": [], "edges": []})
    graph_node_ids = {n["id"] for n in graph.get("nodes", [])}
    channels_cfg = {k: v for k, v in all_channels_cfg.items() if k in graph_node_ids}
    async def event_stream():
        async for ev in run_pipeline_graph(brief, graph, ork, agents_cfg, channels_cfg, history=history):
            yield f"data: {ev}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")

# ===== CREDENTIALS =====
@app.post("/api/projects/{name}/credentials")
async def api_add_credential(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    cid = str(body.get("id", "")).strip()
    if not cid:
        return JSONResponse({"error": "ID required"}, status_code=400)
    cfg.setdefault("credentials", {})[cid] = {
        "label": body.get("label", cid), "provider": body.get("provider", ""),
        "api_key": body.get("api_key", ""), "api_url": body.get("api_url", ""),
    }
    save_project_config(name, cfg)
    return {"status": "ok"}

@app.get("/api/projects/{name}/credentials")
async def api_list_credentials(name: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return cfg.get("credentials", {})

@app.delete("/api/projects/{name}/credentials/{cred_id}")
async def api_delete_credential(name: str, cred_id: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    cfg.get("credentials", {}).pop(cred_id, None)
    save_project_config(name, cfg)
    return {"status": "ok"}

# ===== SKILLS =====
@app.get("/api/skills")
async def api_skills_all():
    return {"skills": BUILTIN_SKILLS}

@app.get("/api/skills/builtin")
async def api_skills_builtin():
    return {"skills": BUILTIN_SKILLS}

@app.post("/api/skills/install")
async def api_skills_install(request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"status": "error", "error": "JSON body required"}, status_code=400)
    project = str(body.get("project", "")).strip()
    agent = str(body.get("agent", "")).strip()
    skill_id = str(body.get("skill_id", "")).strip()
    custom_prompt = body.get("prompt", "")
    cfg = get_project(project)
    if not cfg or agent not in cfg.get("agents", {}):
        return JSONResponse({"status": "error", "error": "Project or agent not found"}, status_code=404)
    skill = next((s for s in BUILTIN_SKILLS if s["id"] == skill_id), None)
    if not skill and not custom_prompt:
        return JSONResponse({"status": "error", "error": "Skill not found"}, status_code=400)
    skill_prompt = custom_prompt or skill.get("prompt", "")
    current = cfg["agents"][agent].get("prompt", "")
    if skill_prompt and skill_prompt not in current:
        cfg["agents"][agent]["prompt"] = current + ("\n\n" if current else "") + skill_prompt
    else:
        cfg["agents"][agent]["prompt"] = skill_prompt or current
    save_project_config(project, cfg)
    return {"status": "ok"}

@app.get("/api/skills/skillhub")
async def api_skills_skillhub():
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://skills.palebluedot.live/api/skills?limit=200")
            if resp.status_code != 200:
                return {"skills": [], "error": f"HTTP {resp.status_code}"}
            data = resp.json()
            raw = data.get("skills", [])
            skills = []
            for s in raw:
                sid = str(s.get("id", "")).replace("/", "-")
                skills.append({
                    "id": sid or s.get("name", ""),
                    "name": str(s.get("name", "")).replace("-", " ").title(),
                    "category": "SkillHub",
                    "description": (s.get("description", "") or "")[:200],
                    "prompt": s.get("description", "") or "Skill da SkillHub: " + s.get("name", ""),
                    "source": str(s.get("githubOwner", "")) + "/" + str(s.get("githubRepo", "")),
                })
            return {"skills": skills, "total": data.get("pagination", {}).get("total", 0)}
    except Exception as e:
        return {"skills": [], "error": str(e)}

@app.post("/api/skills/github")
async def api_skills_github(request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    repo = str(body.get("repo", "")).strip()
    if not repo:
        return JSONResponse({"error": "Repo required"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(f"https://api.github.com/repos/{repo}/contents/skills.json")
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", "")
                if content:
                    import base64
                    decoded = base64.b64decode(content).decode("utf-8")
                    skills_data = json.loads(decoded)
                    return {"skills": skills_data.get("skills", skills_data if isinstance(skills_data, list) else [])}
            return JSONResponse({"skills": [], "error": f"No skills found in {repo}"}, status_code=404)
    except Exception as e:
        return JSONResponse({"skills": [], "error": str(e)}, status_code=500)

@app.post("/api/skills/import")
async def api_skills_import(request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    url = str(body.get("url", "")).strip()
    if not url:
        return JSONResponse({"error": "URL required"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                skills = data.get("skills", data if isinstance(data, list) else [])
                if isinstance(skills, list):
                    return {"skills": skills}
            return JSONResponse({"skills": [], "error": f"HTTP {resp.status_code}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"skills": [], "error": str(e)}, status_code=500)

# ===== AGENT TEMPLATES =====
@app.get("/api/templates")
async def api_templates():
    return {"templates": AGENT_TEMPLATES}

@app.post("/api/templates/apply")
async def api_templates_apply(request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    project = str(body.get("project", "")).strip()
    template_id = str(body.get("template_id", "")).strip()
    agent_name = str(body.get("agent_name", template_id)).strip()
    cfg = get_project(project)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    tpl = next((t for t in AGENT_TEMPLATES if t["id"] == template_id), None)
    if not tpl:
        return JSONResponse({"error": "Template not found"}, status_code=404)
    if agent_name in cfg.get("agents", {}):
        return JSONResponse({"error": "Agent already exists"}, status_code=409)
    cfg.setdefault("agents", {})[agent_name] = {
        "enabled": True, "api_key": "", "api_url": "", "model": "",
        "temperature": 0.3, "prompt": tpl.get("prompt", ""),
        "color": tpl.get("color", "#888"), "tools": [],
    }
    save_project_config(project, cfg)
    return {"status": "ok", "agent": agent_name}

# ===== ASSISTANT =====
async def _exec_assistant_tool(name, args, assistant_project):
    pn = args.get("project", assistant_project)
    if name == "list_projects":
        return {"projects": list_projects()}
    elif name == "get_project_config":
        cfg = get_project(pn)
        if not cfg: return {"error": "Project not found"}
        return {"orchestrator": cfg.get("orchestrator", {}),
                "agents": {k: {kk: vv for kk, vv in v.items() if kk != "api_key"} for k, v in cfg.get("agents", {}).items()}}
    elif name == "create_agent":
        agent, prompt = args.get("agent", ""), args.get("prompt", "")
        color = args.get("color", "#888")
        cfg = get_project(pn)
        if not cfg: return {"error": "Project not found"}
        cfg.setdefault("agents", {})[agent] = {"enabled": True, "api_key": "", "api_url": "", "model": "",
            "temperature": 0.3, "prompt": prompt, "color": color, "tools": []}
        save_project_config(pn, cfg)
        return {"status": "ok", "message": f"Agent @{agent} created"}
    elif name == "update_agent":
        agent = args.get("agent", "")
        cfg = get_project(pn)
        if not cfg or agent not in cfg.get("agents", {}): return {"error": "Project or agent not found"}
        if "prompt" in args: cfg["agents"][agent]["prompt"] = args["prompt"]
        if "model" in args: cfg["agents"][agent]["model"] = args["model"]
        if "temperature" in args: cfg["agents"][agent]["temperature"] = args["temperature"]
        save_project_config(pn, cfg)
        return {"status": "ok", "message": f"Agent @{agent} updated"}
    elif name == "delete_agent":
        agent = args.get("agent", "")
        cfg = get_project(pn)
        if not cfg or agent not in cfg.get("agents", {}): return {"error": "Project or agent not found"}
        del cfg["agents"][agent]
        save_project_config(pn, cfg)
        return {"status": "ok", "message": f"Agent @{agent} deleted"}
    elif name == "connect_agents":
        fro = args.get("from", ""); to = args.get("to", "")
        rtype = args.get("required_type", "optional")
        cfg = get_project(pn)
        if not cfg: return {"error": "Project not found"}
        g = cfg.setdefault("graph", {"nodes": [], "edges": []})
        g["edges"].append({"from": fro, "to": to, "type": rtype})
        save_project_config(pn, cfg)
        return {"status": "ok", "message": f"Connected @{fro} -> @{to}"}
    elif name == "disconnect_agents":
        fro = args.get("from", ""); to = args.get("to", "")
        cfg = get_project(pn)
        if not cfg: return {"error": "Project not found"}
        edges = cfg.get("graph", {}).get("edges", [])
        cfg["graph"]["edges"] = [e for e in edges if e.get("from") != fro or e.get("to") != to]
        save_project_config(pn, cfg)
        return {"status": "ok", "message": "Connection removed"}
    elif name == "update_orchestrator":
        cfg = get_project(pn)
        if not cfg: return {"error": "Project not found"}
        ork = cfg["orchestrator"]
        if "model" in args: ork["model"] = args["model"]
        if "temperature" in args: ork["temperature"] = args["temperature"]
        save_project_config(pn, cfg)
        return {"status": "ok", "message": "Orchestrator updated"}
    elif name == "install_skill":
        agent = args.get("agent", ""); skill_id = args.get("skill_id", "")
        cfg = get_project(pn)
        if not cfg or agent not in cfg.get("agents", {}): return {"error": "Project or agent not found"}
        skill = next((s for s in BUILTIN_SKILLS if s["id"] == skill_id), None)
        custom_prompt = args.get("prompt", "")
        if not skill and not custom_prompt: return {"error": f"Skill '{skill_id}' not found"}
        skill_prompt = custom_prompt or skill["prompt"]
        current = cfg["agents"][agent].get("prompt", "")
        if skill_prompt and skill_prompt not in current:
            cfg["agents"][agent]["prompt"] = current + ("\n\n" if current else "") + skill_prompt
        else:
            cfg["agents"][agent]["prompt"] = skill_prompt or current
        save_project_config(pn, cfg)
        return {"status": "ok", "message": f"Skill '{skill_id}' installed on @{agent}"}
    return {"error": f"Unknown tool '{name}'"}

@app.post("/api/assistant/chat")
async def api_assistant_chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    if not messages: return {"error": "Messages required"}, 400
    assistant_project = body.get("project", "default")
    asst_sys = _get_asst_setting("system_prompt", "You are the Stormo AI assistant.")
    messages = [{"role": "system", "content": asst_sys}] + messages
    headers = {"Authorization": f"Bearer {_get_asst_setting('api_key', '')}", "Content-Type": "application/json",
               "HTTP-Referer": "https://stormx.local", "X-Title": "Stormx AI"}
    async def event_stream():
        try:
            tools_supported = True
            while True:
                payload = {"model": _get_asst_setting('model', 'moonshotai/kimi-k2.6:free'), "messages": messages, "temperature": 0.5, "stream": False}
                if tools_supported: payload["tools"] = _get_asst_setting('tools', [])
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(_get_asst_setting('api_url', 'https://openrouter.ai/api/v1/chat/completions'), json=payload, headers=headers)
                    if resp.status_code != 200:
                        err_body = await resp.aread()
                        err_str = err_body.decode()[:300]
                        if resp.status_code in (400, 404) and ("tool" in err_str.lower() or "support tool" in err_str.lower()) and tools_supported:
                            tools_supported = False
                            continue
                        yield f"data: {json.dumps({'type': 'error', 'content': f'API Error ({resp.status_code}): {err_str}'})}\n\n"
                        return
                    data = resp.json()
                choice = data["choices"][0]
                msg = choice["message"]
                messages.append(msg)
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    msgs_no_tools = [m for m in messages if m.get("role") != "tool"]
                    payload["messages"] = msgs_no_tools
                    payload.pop("tools", None)
                    payload["stream"] = True
                    async with httpx.AsyncClient(timeout=60) as client:
                        async with client.stream("POST", _get_asst_setting('api_url', 'https://openrouter.ai/api/v1/chat/completions'), json=payload, headers=headers) as sresp:
                            async for line in sresp.aiter_lines():
                                if not line: continue
                                if line.startswith("data: "):
                                    raw = line[6:].strip()
                                    if raw == "[DONE]": break
                                    try:
                                        c = json.loads(raw)
                                        delta = c.get("choices", [{}])[0].get("delta", {})
                                        if content := delta.get("content", ""):
                                            yield f"data: {json.dumps({'type': 'chunk', 'content': content})}\n\n"
                                    except: continue
                    yield f"data: {json.dumps({'type': 'action', 'action': 'refresh', 'label': 'Updating interface...'})}\n\n"
                    return
                for tc in tool_calls:
                    func = tc["function"]
                    nm, raw_args = func["name"], func.get("arguments", "{}")
                    try: args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except: args = {}
                    result = await _exec_assistant_tool(nm, args, assistant_project)
                    label_map = {"create_agent": "Creating agent", "update_agent": "Modifying agent",
                        "delete_agent": "Deleting agent", "connect_agents": "Connecting agents",
                        "disconnect_agents": "Removing connection", "update_orchestrator": "Updating orchestrator",
                        "list_projects": "Reading projects", "get_project_config": "Reading configuration",}
                    yield f"data: {json.dumps({'type': 'action', 'action': nm, 'args': args, 'result': result, 'label': label_map.get(nm, 'Executing ' + nm)})}\n\n"
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

# ===== ANALYTICS =====
@app.get("/api/analytics")
async def api_analytics():
    projects = list_projects()
    total_agents = 0
    by_project = {}
    for p in projects:
        cfg = get_project(p["name"])
        agent_count = len(cfg.get("agents", {})) if cfg else 0
        total_agents += agent_count
        by_project[p["name"]] = {"agents": agent_count}
    return {"total_projects": len(projects), "total_agents": total_agents,
            "by_project": by_project}

# ===== SHARE / EXPORT =====
@app.post("/api/projects/{name}/share")
async def api_share_project(name: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return {"share_token": "shareable_project", "data": {"project": name, "config": cfg, "version": 1}}

@app.post("/api/projects/import")
async def api_import_project(request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    data = body.get("data", {})
    pname = str(data.get("project", body.get("name", "imported_project"))).strip()
    cfg_data = data.get("config", {})
    if not isinstance(cfg_data, dict) or not cfg_data:
        return JSONResponse({"error": "Invalid project data"}, status_code=400)
    if not is_valid_project_name(pname):
        pname = "imported_project"
    existing = get_project(pname)
    if existing:
        import time as _t
        base = pname
        counter = 1
        while get_project(base + "_" + str(counter)):
            counter += 1
        pname = base + "_" + str(counter)
    if create_project(pname, cfg_data):
        return {"status": "ok", "project": pname}
    return JSONResponse({"error": "Unable to create project"}, status_code=400)

# # ===== OBSERVABILITY =====
# @app.post("/api/observability/report")
# async def api_observability_report(request: Request):
#     body = await _read_json(request)
#     if body is None:
#         return JSONResponse({"status": "error", "error": "JSON body required"}, status_code=400)
#     project = str(body.get("project", "unknown")).strip() or "unknown"
#     pipeline_time = float(body.get("time", 0) or 0)
#     return {"status": "ok"}

# @app.get("/api/observability")
# async def api_observability_get():
#     return {"total_chats": 0, "total_time": 0.0, "by_project": {}}

# ===== EXTERNAL CALL LOG (n8n, webhook, MCP calls) =====
WEBHOOK_LOG = []
MAX_LOG_ENTRIES = 200

@app.get("/api/log")
async def api_log_get():
    from agents import external_call_log
    combined = []
    for e in INCOMING_LOG:
        d = dict(e); d.setdefault("source", "incoming"); d.setdefault("direction", "in"); combined.append(d)
    for e in WEBHOOK_LOG:
        d = dict(e); d.setdefault("source", "webhook"); d.setdefault("direction", "in"); combined.append(d)
    for e in external_call_log:
        d = dict(e); d.setdefault("source", "mcp"); d.setdefault("direction", "out"); combined.append(d)
    combined.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return {"log": combined[:300], "total": len(combined)}

@app.post("/api/log/clear")
async def api_log_clear():
    from agents import external_call_log
    INCOMING_LOG.clear()
    WEBHOOK_LOG.clear()
    external_call_log.clear()
    return {"status": "ok"}

# ===== TOOL EXECUTION =====
@app.post("/api/projects/{name}/tools/execute")
async def api_tools_execute(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    code = body.get("code", "")
    params = body.get("params", {})
    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)
    result = await execute_tool(code, params)
    return {"result": result}

@app.post("/api/projects/{name}/tools/mcp-call")
async def api_mcp_call(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    tool_name = body.get("tool_name", "")
    params = body.get("params", {})
    if not tool_name:
        return JSONResponse({"error": "No tool_name provided"}, status_code=400)
    result = await call_mcp_tool(tool_name, params)
    return {"result": result}

# ===== AGENT TEST =====
@app.post("/api/projects/{name}/agents/{agent}/test")
async def api_agent_test(name: str, agent: str, request: Request):
    body = await _read_json(request)
    input_text = body.get("input", "") if body else ""
    cfg = get_project(name)
    if not cfg or agent not in cfg.get("agents", {}):
        return JSONResponse({"error": "Project or agent not found"}, status_code=404)
    ag = cfg["agents"][agent]
    ork = cfg.get("orchestrator", {})
    r = _resolve_cred(ag, cfg)
    api_key = r.get("api_key") or ork.get("api_key", "")
    api_url = r.get("api_url") or ork.get("api_url", "")
    model = ag.get("model") or ork.get("model", "")
    prompt = ag.get("prompt", "")
    temperature = ag.get("temperature", ork.get("temperature", 0.3))
    output_parts = []
    async for chunk in call_model_stream(api_key, api_url, model, prompt, input_text, temperature):
        data = json.loads(chunk)
        if data["type"] == "chunk": output_parts.append(data["content"])
    return {"output": "".join(output_parts)}

# ===== AGENT SUBMIT (form) =====
@app.post("/api/projects/{name}/agents/{agent}/submit")
async def api_agent_submit(name: str, agent: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    form_data = body.get("form", {})
    cfg = get_project(name)
    if not cfg or agent not in cfg.get("agents", {}):
        return JSONResponse({"error": "Project or agent not found"}, status_code=404)
    ag = cfg["agents"][agent]
    ork = cfg.get("orchestrator", {})
    r = _resolve_cred(ag, cfg)
    api_key = r.get("api_key") or ork.get("api_key", "")
    api_url = r.get("api_url") or ork.get("api_url", "")
    model = ag.get("model") or ork.get("model", "")
    prompt = ag.get("prompt", "")
    temperature = ag.get("temperature", ork.get("temperature", 0.3))
    user_msg = f"Dati ricevuti dal form:\n{json.dumps(form_data, indent=2)}\n\n{prompt}"
    output_parts = []
    async for chunk in call_model_stream(api_key, api_url, model, prompt, user_msg, temperature):
        data = json.loads(chunk)
        if data["type"] == "chunk": output_parts.append(data["content"])
    return {"output": "".join(output_parts)}

# ===== AGENT PROMPT HISTORY =====
@app.get("/api/projects/{name}/agents/{agent}/history")
async def api_agent_history(name: str, agent: str):
    hist = get_history(name)
    agent_history = [h for h in hist if h.get("agent") == agent]
    return {"history": agent_history}

@app.post("/api/projects/{name}/agents/{agent}/restore-prompt")
async def api_agent_restore_prompt(name: str, agent: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    idx = int(body.get("index", 0))
    hist = get_history(name)
    agent_history = [h for h in hist if h.get("agent") == agent]
    if idx < 0 or idx >= len(agent_history):
        return JSONResponse({"error": "Invalid index"}, status_code=400)
    entry = agent_history[idx]
    cfg = get_project(name)
    if not cfg or agent not in cfg.get("agents", {}):
        return JSONResponse({"error": "Project or agent not found"}, status_code=404)
    cfg["agents"][agent]["prompt"] = entry.get("prompt", cfg["agents"][agent].get("prompt", ""))
    save_project_config(name, cfg)
    return {"status": "ok"}

# ===== HISTORY =====
@app.post("/api/projects/{name}/history")
async def api_add_history(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    return {"history": append_to_history(name, body)}

@app.get("/api/projects/{name}/history")
async def api_get_history(name: str):
    return {"history": get_history(name)}

@app.delete("/api/projects/{name}/history")
async def api_clear_history(name: str):
    clear_history(name)
    return {"status": "ok"}

# ===== MCP TOOLS =====
@app.post("/api/projects/{name}/mcp/register")
async def api_mcp_register(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    tool_name = str(body.get("name", "")).strip()
    url = str(body.get("url", "")).strip()
    description = body.get("description", "")
    api_key = body.get("api_key", "")
    if not tool_name or not url:
        return JSONResponse({"error": "Name and URL required"}, status_code=400)
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    tools = cfg.setdefault("mcp_tools", [])
    existing = next((t for t in tools if t["name"] == tool_name), None)
    if existing:
        existing.update({"url": url, "description": description, "api_key": api_key})
    else:
        tools.append({"name": tool_name, "url": url, "description": description, "api_key": api_key})
    register_mcp_tool(tool_name, url, description, api_key=api_key)
    save_project_config(name, cfg)
    return {"status": "ok"}

@app.post("/api/projects/{name}/mcp/setkey")
async def api_mcp_setkey(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    tool_name = str(body.get("tool_name", "")).strip()
    api_key = body.get("api_key", "")
    if not tool_name:
        return JSONResponse({"error": "tool_name required"}, status_code=400)
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    for t in cfg.get("mcp_tools", []):
        if t["name"] == tool_name:
            t["api_key"] = api_key
            break
    if tool_name in MCP_REGISTRY:
        MCP_REGISTRY[tool_name]["api_key"] = api_key
    save_project_config(name, cfg)
    return {"status": "ok"}

@app.post("/api/projects/{name}/mcp/authorize")
async def api_mcp_authorize(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    tool_name = str(body.get("tool_name", "")).strip()
    agent_name = str(body.get("agent_name", "")).strip()
    authorized = bool(body.get("authorized", True))
    if not tool_name or not agent_name:
        return JSONResponse({"error": "tool_name and agent_name required"}, status_code=400)
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    agent = cfg.get("agents", {}).get(agent_name)
    if not agent:
        return JSONResponse({"error": f"Agent '{agent_name}' not found"}, status_code=404)
    agent.setdefault("mcp_tools", [])
    if authorized:
        if not any(t.get("name") == tool_name for t in agent["mcp_tools"]):
            tool_cfg = next((t for t in cfg.get("mcp_tools", []) if t["name"] == tool_name), {})
            agent["mcp_tools"].append({"name": tool_name, "url": tool_cfg.get("url", ""), "description": tool_cfg.get("description", "")})
    else:
        agent["mcp_tools"] = [t for t in agent["mcp_tools"] if t.get("name") != tool_name]
    save_project_config(name, cfg)
    return {"status": "ok"}

# ===== CHECKPOINTS =====
@app.get("/api/projects/{name}/checkpoints")
async def api_list_checkpoints(name: str):
    return {"checkpoints": get_checkpoints(name)}

@app.post("/api/projects/{name}/checkpoints")
async def api_save_checkpoint(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    save_checkpoint(name, body)
    return {"status": "ok"}

@app.delete("/api/projects/{name}/checkpoints")
async def api_delete_checkpoints(name: str):
    delete_checkpoints(name)
    return {"status": "ok"}

@app.post("/api/projects/{name}/checkpoints/restore")
async def api_restore_checkpoint(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    step_index = int(body.get("step_index", 0))
    cp = restore_from_checkpoint(name, step_index)
    if cp:
        return {"checkpoint": cp}
    return JSONResponse({"error": "Checkpoint not found"}, status_code=404)

# ===== LEGACY WEBHOOK =====
@app.post("/api/projects/{name}/webhook")
async def api_webhook_receive(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    target = str(body.get("target", "")).strip()
    payload = body.get("payload", {})
    if target and target in cfg.get("agents", {}):
        ag = cfg["agents"][target]
        r = _resolve_cred(ag, cfg)
        ork = cfg.get("orchestrator", {})
        api_key = r.get("api_key") or ork.get("api_key", "")
        api_url = r.get("api_url") or ork.get("api_url", "")
        model = ag.get("model") or ork.get("model", "")
        output = []
        async for chunk in call_model_stream(api_key, api_url, model, ag.get("prompt", ""), json.dumps(payload),
                                              ag.get("temperature", ork.get("temperature", 0.3))):
            data = json.loads(chunk)
            if data["type"] == "chunk": output.append(data["content"])
        return {"output": "".join(output)}
    return {"status": "received", "note": "No target specified"}

# ===== RAG ENDPOINTS (vettoriale con ChromaDB) =====
@app.post("/api/projects/{name}/rag/ingest")
async def api_rag_ingest(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    content = str(body.get("content", ""))
    filename = str(body.get("filename", "untitled.txt"))
    source = str(body.get("source", "manual"))
    chunk_size = int(body.get("chunk_size", 500))
    overlap = int(body.get("overlap", 50))
    if not content.strip():
        return JSONResponse({"error": "Empty content"}, status_code=400)
    try:
        result = rag_ingest(name, content, filename, source, chunk_size, overlap)
        return result
    except Exception as e:
        logger.exception("RAG ingest error")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/projects/{name}/rag/query")
async def api_rag_query(name: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    query_text = str(body.get("query", ""))
    top_k = int(body.get("top_k", 5))
    if not query_text.strip():
        return JSONResponse({"error": "Empty query"}, status_code=400)
    try:
        results = rag_query(name, query_text, top_k)
        return {"results": results, "total": len(results)}
    except Exception as e:
        logger.exception("RAG query error")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/projects/{name}/rag/documents")
async def api_rag_docs(name: str):
    try:
        docs = rag_list(name)
        return {"documents": docs}
    except Exception as e:
        logger.exception("RAG list error")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.delete("/api/projects/{name}/rag/documents/{doc_id}")
async def api_rag_delete(name: str, doc_id: str):
    try:
        rag_delete(name, doc_id)
        return {"status": "ok"}
    except Exception as e:
        logger.exception("RAG delete error")
        return JSONResponse({"error": str(e)}, status_code=500)

# ===== TRIGGERS / WEBHOOK URL =====
@app.get("/api/projects/{name}/webhook-url/{agent}")
async def api_webhook_url(name: str, agent: str):
    return {"url": f"{os.environ.get('PUBLIC_URL', 'http://localhost:8080')}/api/webhook/{name}/{agent}"}

@app.post("/api/webhook/{name}/{agent}")
async def api_webhook_receive_agent(name: str, agent: str, request: Request):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    if agent not in cfg.get("agents", {}):
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    ag = cfg["agents"][agent]
    r = _resolve_cred(ag, cfg)
    ork = cfg.get("orchestrator", {})
    api_key = r.get("api_key") or ork.get("api_key", "")
    api_url = r.get("api_url") or ork.get("api_url", "")
    model = ag.get("model") or ork.get("model", "")
    try:
        ct = request.headers.get("content-type", "")
        if "json" in ct:
            body = await request.json()
            payload = json.dumps(body, indent=2)
        elif "form" in ct:
            form = await request.form()
            payload = json.dumps(dict(form), indent=2)
        else:
            text = await request.body()
            payload = text.decode("utf-8", errors="replace")
    except Exception as e:
        payload = f"(error reading payload: {e})"
    WEBHOOK_LOG.append({"ts": time.time(), "method": "POST", "url": f"/webhook/{name}/{agent}",
                        "status": 200, "payload": payload[:200], "source": "webhook", "direction": "in",
                        "project": name, "channel_id": agent})
    log_incoming("webhook", name, agent, text=payload[:200], payload=payload[:300], status=200, method="POST")
    while len(WEBHOOK_LOG) > MAX_LOG_ENTRIES: WEBHOOK_LOG.pop(0)
    prompt = ag.get("prompt", "")
    user_msg = f"Trigger received from external channel for @{agent}.\n\nPayload:\n{payload}\n\n{prompt}"
    output_parts = []
    async for chunk in call_model_stream(api_key, api_url, model, prompt, user_msg,
                                          ag.get("temperature", ork.get("temperature", 0.3))):
        data = json.loads(chunk)
        if data["type"] == "chunk": output_parts.append(data["content"])
    return {"status": "ok", "agent": agent, "output": "".join(output_parts)}

# ===== HEALTH =====
@app.get("/api/health")
async def api_health():
    return {"status": "ok", "projects": len(list_projects())}

# ===== TELEGRAM WEBHOOK =====
@app.post("/api/telegram/webhook/{project}/{channel_id}")
async def api_telegram_webhook(project: str, channel_id: str, request: Request):
    """Receive Telegram update via webhook and trigger pipeline."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False}
    cfg = get_project(project)
    if not cfg:
        return {"ok": False}
    ch = cfg.get("channels", {}).get(channel_id)
    if not ch:
        return {"ok": False}
    msg = body.get("message", {})
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    text = msg.get("text", msg.get("caption", ""))
    sender = msg.get("from", {}).get("first_name", "") or msg.get("from", {}).get("username", "")
    if not chat_id or not text:
        log_incoming("telegram", project, channel_id, text="", sender=sender,
                     payload=json.dumps(body)[:300], status=200)
        return {"ok": True}
    log_incoming("telegram", project, channel_id, text=text, sender=sender,
                 payload=json.dumps(body)[:300], status=200)
    token = channel_registry.resolve_bot_token(project, ch.get("config", {}))
    if token:
        tg = channel_registry.register_telegram(token, project, channel_id)
        await channel_registry.dispatch_message(project, channel_id, chat_id, text)
    asyncio.create_task(_handle_channel_trigger(project, channel_id, chat_id, text))
    return {"ok": True}

# ===== DISCORD WEBHOOK (Interactions) =====
@app.post("/api/discord/webhook/{project}/{channel_id}")
async def api_discord_webhook(project: str, channel_id: str, request: Request):
    """Receive Discord Interaction webhook and trigger pipeline on messages."""
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        body = {}
    cfg = get_project(project)
    if not cfg:
        log_incoming("discord", project, channel_id, text="", payload="project not found", status=404)
        return JSONResponse({"error": "Project not found"}, status_code=404)
    ch = cfg.get("channels", {}).get(channel_id)
    if not ch:
        log_incoming("discord", project, channel_id, text="", payload="channel not found", status=404)
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    itype = body.get("type")
    # Ping verification (Discord requires type 1 ACK)
    if itype == 1:
        log_incoming("discord", project, channel_id, text="[ping verification]",
                     payload=json.dumps(body)[:300], status=200)
        return {"type": 1}
    # Application command / message content
    data = body.get("data", {}) or {}
    text = data.get("content", "")
    sender = (body.get("member", {}) or {}).get("user", {}).get("username", "") \
        or (body.get("user", {}) or {}).get("username", "")
    options = data.get("options", [])
    if not text and options:
        text = " ".join(str(o.get("value", "")) for o in options)
    log_incoming("discord", project, channel_id, text=text, sender=sender,
                 payload=json.dumps(body)[:300], status=200)
    target_channel = ch.get("config", {}).get("channel_id", "")
    if text:
        asyncio.create_task(_handle_channel_trigger(project, channel_id, target_channel or channel_id, text))
        # Acknowledge with the message content so Discord shows a reply
        return {"type": 4, "data": {"content": "Messaggio ricevuto ed elaborato."}}
    return {"type": 5}

# ===== WHATSAPP WEBHOOK (Cloud API) =====
@app.get("/api/whatsapp/webhook/{project}/{channel_id}")
async def api_whatsapp_webhook_verify(project: str, channel_id: str, request: Request):
    """WhatsApp Cloud API verification handshake."""
    cfg = get_project(project)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    ch = cfg.get("channels", {}).get(channel_id)
    if not ch:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    verify_token = ch.get("config", {}).get("verify_token", "")
    mode = request.query_params.get("hub.mode", "")
    challenge = request.query_params.get("hub.challenge", "")
    token = request.query_params.get("hub.verify_token", "")
    if mode == "subscribe" and (not verify_token or token == verify_token):
        log_incoming("whatsapp", project, channel_id, text="[verify handshake]",
                     payload=f"mode={mode}", status=200)
        return PlainTextResponse(challenge)
    log_incoming("whatsapp", project, channel_id, text="[verify failed]",
                 payload=f"token={token}", status=403)
    return JSONResponse({"error": "Verification failed"}, status_code=403)

@app.post("/api/whatsapp/webhook/{project}/{channel_id}")
async def api_whatsapp_webhook_receive(project: str, channel_id: str, request: Request):
    """Receive WhatsApp Cloud API inbound messages and trigger pipeline."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    cfg = get_project(project)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    ch = cfg.get("channels", {}).get(channel_id)
    if not ch:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    messages = WhatsAppChannel.parse_inbound(body)
    wa = None
    api_token = ch.get("config", {}).get("api_token", "")
    phone_number_id = ch.get("config", {}).get("phone_number_id", "")
    if api_token and phone_number_id:
        wa = WhatsAppChannel(api_token, phone_number_id)
    for m in messages:
        log_incoming("whatsapp", project, channel_id, text=m.get("text", ""),
                     sender=m.get("from", "") or m.get("name", ""),
                     payload=json.dumps(body)[:300], status=200)
        if m.get("text"):
            asyncio.create_task(_handle_channel_trigger(project, channel_id, m.get("from", ""), m.get("text", "")))
    if not messages:
        log_incoming("whatsapp", project, channel_id, text="[status/notification]",
                     payload=json.dumps(body)[:300], status=200)
    return {"status": "ok"}

# ===== GENERIC CHANNEL WEBHOOK =====
@app.post("/api/webhook/channel/{project}/{channel_id}")
async def api_channel_webhook_generic(project: str, channel_id: str, request: Request):
    """Generic webhook that logs an inbound external call and optionally triggers the pipeline."""
    ct = request.headers.get("content-type", "")
    try:
        if "json" in ct:
            body = await request.json()
            payload_str = json.dumps(body)[:300]
            text = ""
            if isinstance(body, dict):
                text = str(body.get("text", body.get("message", body.get("content", ""))))[:300]
        else:
            raw = await request.body()
            payload_str = raw.decode("utf-8", errors="replace")[:300]
            text = payload_str
    except Exception as e:
        payload_str = f"(error reading payload: {e})"
        text = ""
    cfg = get_project(project)
    if not cfg:
        log_incoming("webhook", project, channel_id, text=text, payload=payload_str, status=404)
        return JSONResponse({"error": "Project not found"}, status_code=404)
    ch = cfg.get("channels", {}).get(channel_id, {})
    source = ch.get("type", "webhook") if ch else "webhook"
    sender = request.query_params.get("from", "") or request.headers.get("x-webhook-source", "")
    log_incoming(source, project, channel_id, text=text, sender=sender,
                 payload=payload_str, status=200, method="POST")
    if text:
        target_chat = ch.get("config", {}).get("channel_id", "") or channel_id
        asyncio.create_task(_handle_channel_trigger(project, channel_id, target_chat, text))
    return {"status": "ok", "source": source}


# ===== CHANNEL CONFIG MANAGEMENT =====
@app.get("/api/projects/{name}/channels")
async def api_list_channels(name: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return cfg.get("channels", {})

@app.post("/api/projects/{name}/channels/{channel_id}")
async def api_update_channel(name: str, channel_id: str, request: Request):
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    channels = cfg.setdefault("channels", {})
    channels[channel_id] = body
    save_project_config(name, cfg)
    return {"status": "ok"}

@app.delete("/api/projects/{name}/channels/{channel_id}")
async def api_delete_channel(name: str, channel_id: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    cfg.get("channels", {}).pop(channel_id, None)
    save_project_config(name, cfg)
    return {"status": "ok"}

@app.post("/api/projects/{name}/channels/{channel_id}/register-webhook")
async def api_register_channel_webhook(name: str, channel_id: str):
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    ch = cfg.get("channels", {}).get(channel_id)
    if not ch:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    public_url = os.environ.get("PUBLIC_URL", "")
    if not public_url:
        return JSONResponse({"error": "PUBLIC_URL environment variable not set"}, status_code=400)
    if ch.get("type") == "telegram":
        token = channel_registry.resolve_bot_token(name, ch.get("config", {}))
        if not token:
            return JSONResponse({"error": "bot_token not configured"}, status_code=400)
        tg = channel_registry.register_telegram(token, name, channel_id)
        webhook_url = f"{public_url.rstrip('/')}/api/telegram/webhook/{name}/{channel_id}"
        ok = await tg.set_webhook(webhook_url)
        if ok:
            return {"status": "ok", "webhook_url": webhook_url}
        return JSONResponse({"error": "Webhook registration failed"}, status_code=500)
    elif ch.get("type") == "discord":
        webhook_url = f"{public_url.rstrip('/')}/api/discord/webhook/{name}/{channel_id}"
        return {"status": "ok", "webhook_url": webhook_url,
                "note": "Configure this URL as Discord Interactions Endpoint URL in the Discord Developer Portal."}
    elif ch.get("type") == "whatsapp":
        webhook_url = f"{public_url.rstrip('/')}/api/whatsapp/webhook/{name}/{channel_id}"
        return {"status": "ok", "webhook_url": webhook_url,
                "note": "Configure this URL in the WhatsApp Cloud API webhook subscription."}
    else:
        webhook_url = f"{public_url.rstrip('/')}/api/webhook/channel/{name}/{channel_id}"
        return {"status": "ok", "webhook_url": webhook_url}

@app.post("/api/projects/{name}/channels/{channel_id}/test")
async def api_test_channel(name: str, channel_id: str, request: Request):
    """Send a test message through a channel."""
    body = await _read_json(request)
    if body is None:
        return JSONResponse({"error": "JSON body required"}, status_code=400)
    cfg = get_project(name)
    if not cfg:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    ch = cfg.get("channels", {}).get(channel_id)
    if not ch:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    text = body.get("text", "Test message from Stormo AI")
    from agents import _send_channel_reply
    await _send_channel_reply(name, channel_id, ch, text)
    return {"status": "ok", "sent": text[:100]}

# ===== STATIC FILES (after all API routes) =====
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

# ===== MAIN =====
def main():
    port = int(os.environ.get("PORT", 7777))
    print(f"==> Stormo AI App: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")

if __name__ == "__main__":
    main()
