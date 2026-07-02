import json, os, time, threading, copy, re, shutil, logging

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_FILE = os.path.join(CONFIG_DIR, "stormx_projects.json")
HISTORY_DIR = os.path.join(CONFIG_DIR, "history")
CHECKPOINT_DIR = os.path.join(CONFIG_DIR, "checkpoints")
RAG_DIR = os.path.join(CONFIG_DIR, "rag_data")
os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(CHECKPOINT_DIR, exist_ok=True)

_lock = threading.RLock()
logger = logging.getLogger("stormx.config")

PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

def is_valid_project_name(name: str) -> bool:
    return bool(name and PROJECT_NAME_RE.match(name))


def validate_graph(graph: dict) -> dict:
    """Validate and sanitize graph structure.
    - Remove self-loop edges (from == to)
    - Deduplicate edges
    - Ensure required fields exist
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    valid_edges = []
    seen = set()
    for e in edges:
        f = e.get("from", "")
        t = e.get("to", "")
        if not f or not t:
            continue
        if f == t:
            continue
        key = f"{f}->{t}"
        if key in seen:
            continue
        seen.add(key)
        valid_edges.append({
            "from": f,
            "to": t,
            "type": e.get("type", "optional"),
        })
    return {"nodes": nodes, "edges": valid_edges}

def _atomic_write(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

# MODEL_PRICES = {
#     "deepseek-chat": {"input": 0.00014, "output": 0.00028},
#     "deepseek-coder": {"input": 0.00014, "output": 0.00028},
#     "gpt-4o": {"input": 0.0025, "output": 0.01},
#     "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
#     "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
#     "claude-sonnet-4": {"input": 0.003, "output": 0.015},
#     "claude-haiku-3-5": {"input": 0.0008, "output": 0.004},
#     "gemini-2.5-flash": {"input": 0.00015, "output": 0.0006},
#     "gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
#     "gemini-2.5-pro": {"input": 0.00125, "output": 0.005},
#     "openrouter/auto": {"input": 0.001, "output": 0.002},
# }

DEFAULT_AGENT_CONFIGS = {
    "scriptwriter": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.3,
        "prompt": "You are a copywriter and screenwriter specialized in video. Write engaging scripts optimized for the video medium. Consider: target, duration, tone, hook, narrative structure and CTA.",
        "color": "#00d4aa",
        "tools": [{"name":"get_video_templates","description":"Script templates for video formats",
            "code":"def run(params):\n    fmt=params.get('format','tutorial')\n    tpl={'tutorial':'Hook->Problema->Soluzione->CTA','review':'Hook->Contesto->Pro/Contro->Verdetto'}\n    return tpl.get(fmt,tpl['tutorial'])"}],
    },
    "storyboarder": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.3,
        "prompt": "You are a storyboard artist. Generate detailed scene-by-scene descriptions: shot, angle, duration, transitions, visual elements, on-screen text, directing notes.",
        "color": "#7c3aed", "tools": [],
    },
    "voiceover": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.3,
        "prompt": "You are a voiceover copywriter. Rewrite scripts for oral reading: short sentences, pauses, clear pronunciation. Add tone, speed and emphasis.",
        "color": "#f59e0b", "tools": [],
    },
    "captions": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.2,
        "prompt": "You are a subtitling expert. Generate SRT/VTT files. Max 42 chars/line, 2 lines, lip sync.",
        "color": "#06b6d4", "tools": [],
    },
    "musicsound": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.4,
        "prompt": "You are a sound designer. Recommend music genre, BPM, sound effects, royalty-free libraries.",
        "color": "#ec4899", "tools": [],
    },
    "thumbnails": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.4,
        "prompt": "You are a thumbnail designer. Generate visual briefs: composition, colors, text, fonts, color psychology.",
        "color": "#fb923c", "tools": [],
    },
    "ffmpeg": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.1,
        "prompt": "You are an FFmpeg expert. Generate commands for cutting, merging, encoding, overlay, concat, filters. Explain each parameter.",
        "color": "#ef4444", "tools": [],
    },
    "publishing": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.3,
        "prompt": "You are a video publisher. Generate SEO title, description, tags, hashtags, chapters, cards. Adapt for each platform.",
        "color": "#a855f7", "tools": [],
    },
    "gestore-youtube": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.3,
        "prompt": "You are a YouTube-specialized assistant. To search for videos from a channel: 1) search channel with /search, q=NAME, type=channel, part=snippet, maxResults=1 2) extract channelId 3) search videos with /search, channelId=..., part=snippet, order=date, maxResults=1. DO NOT use type=video. ALWAYS include part=snippet in every call.",
        "color": "#00d4aa", "tools": [],
    },
    "annotatore-notion": {"enabled": True, "api_key": "", "api_url": "", "model": "", "temperature": 0.3,
        "prompt": "You are a Notion-specialized assistant. To create pages: search for a parent page with /search and empty query, take the first result, then create the page with /pages (method POST) with properties in the body. Do not use database.",
        "color": "#7c3aed", "tools": [],
    },
}

def _default_graph_nodes(agents_dict):
    nodes = [{"id":"orchestrator","type":"orchestrator","label":"Orchestrator","color":"#60a5fa","x":280,"y":50}]
    agents = [n for n,c in agents_dict.items() if c.get("enabled",True)]
    cols = 4; rad = 55; start_x = 280 - (min(len(agents),cols)-1)*rad
    for i, name in enumerate(agents):
        col = i % cols; row = i // cols
        x = start_x + col * rad * 2 + 20
        y = 160 + row * 100
        c = agents_dict[name].get("color","#888")
        nodes.append({"id":name,"type":"agent","label":f"@{name}","color":c,"x":x,"y":y})
    return nodes

def _default_edges(agents_dict):
    agents = [n for n,c in agents_dict.items() if c.get("enabled",True)]
    return [{"from":"orchestrator","to":name} for name in agents]

DEFAULT_PROJECT = {
    "name": "",
    "orchestrator": {"api_key": "", "api_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "temperature": 0.3},
    "agents": {},
    "channels": {},
    "graph": {"nodes": [], "edges": []},
}

def _load_all():
    if not os.path.exists(PROJECTS_FILE):
        d = dict(DEFAULT_PROJECT, name="default")
        _save_all({"default": d})
        return {"default": d}
    try:
        with _lock:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load projects file: {e}")
        bak = PROJECTS_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e2:
                logger.error(f"Failed to load backup: {e2}")
        d = dict(DEFAULT_PROJECT, name="default")
        _save_all({"default": d})
        return {"default": d}

def _save_all(projects):
    with _lock:
        if os.path.exists(PROJECTS_FILE):
            try:
                shutil.copy2(PROJECTS_FILE, PROJECTS_FILE + ".bak")
            except OSError:
                pass
        _atomic_write(PROJECTS_FILE, projects)

def list_projects():
    projects = _load_all()
    out = []
    for n, p in projects.items():
        ork = p.get("orchestrator") or {}
        out.append({"name": n, "model": ork.get("model", ""), "url": ork.get("api_url", "")})
    return sorted(out, key=lambda x: x["name"].lower())

def get_project(name):
    if not is_valid_project_name(name):
        return None
    return _load_all().get(name)

def create_project(name, cfg=None):
    if not is_valid_project_name(name):
        return False
    with _lock:
        projects = _load_all()
        if name in projects:
            return False
        if cfg and isinstance(cfg, dict):
            p = copy.deepcopy(cfg)
            p["name"] = name
        else:
            p = copy.deepcopy(DEFAULT_PROJECT)
            p["name"] = name
        projects[name] = p
        _save_all(projects)
        return True

def delete_project(name):
    if name == "default" or not is_valid_project_name(name):
        return False
    with _lock:
        projects = _load_all()
        if name not in projects:
            return False
        del projects[name]
        _save_all(projects)
        for d in [HISTORY_DIR, CHECKPOINT_DIR]:
            f = os.path.join(d, f"{name}.json")
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        rag_path = os.path.join(RAG_DIR, name)
        if os.path.isdir(rag_path):
            try:
                shutil.rmtree(rag_path)
            except OSError:
                pass
        return True

def rename_project(old_name, new_name):
    if old_name == "default" or not is_valid_project_name(old_name) or not is_valid_project_name(new_name):
        return False
    with _lock:
        projects = _load_all()
        if old_name not in projects or new_name in projects:
            return False
        projects[new_name] = projects.pop(old_name)
        projects[new_name]["name"] = new_name
        _save_all(projects)
        for d in [HISTORY_DIR, CHECKPOINT_DIR]:
            old_f = os.path.join(d, f"{old_name}.json")
            new_f = os.path.join(d, f"{new_name}.json")
            if os.path.exists(old_f):
                try:
                    os.replace(old_f, new_f)
                except OSError:
                    pass
        old_rag = os.path.join(RAG_DIR, old_name)
        new_rag = os.path.join(RAG_DIR, new_name)
        if os.path.isdir(old_rag):
            try:
                os.replace(old_rag, new_rag)
            except OSError:
                pass
        return True

def save_project_config(name, config_data):
    if not is_valid_project_name(name):
        return False
    with _lock:
        projects = _load_all()
        if name not in projects:
            return False
        p = copy.deepcopy(config_data)
        p["name"] = name
        projects[name] = p
        _save_all(projects)
        return True

def reset_project_config(name):
    if not is_valid_project_name(name):
        return None
    with _lock:
        projects = _load_all()
        if name not in projects:
            return None
        p = copy.deepcopy(DEFAULT_PROJECT)
        p["name"] = name
        projects[name] = p
        _save_all(projects)
        return dict(p)

# ===== GRAPH =====
def save_graph(name, graph_data):
    if not is_valid_project_name(name):
        return False
    with _lock:
        projects = _load_all()
        if name not in projects:
            return False
        graph = validate_graph(copy.deepcopy(graph_data))
        projects[name]["graph"] = graph
        # Sync agents from graph nodes
        nodes = graph.get("nodes", [])
        node_ids = {n["id"] for n in nodes}
        agents = projects[name].setdefault("agents", {})
        channels = projects[name].setdefault("channels", {})
        # Remove stale agents/channels not in graph
        for aid in list(agents.keys()):
            if aid not in node_ids:
                del agents[aid]
        for cid in list(channels.keys()):
            if cid not in node_ids:
                del channels[cid]
        for node in nodes:
            if node["type"] == "agent":
                nid = node["id"]
                if nid not in agents:
                    new_agent = copy.deepcopy(DEFAULT_AGENT_CONFIGS.get(nid, {}))
                    new_agent["color"] = node.get("color", "#888")
                    agents[nid] = new_agent
            elif node["type"] == "channel":
                nid = node["id"]
                if nid not in channels:
                    ch_type = node.get("channel_type", "telegram")
                    channels[nid] = {"type": ch_type, "config": {}, "enabled": True}
        _save_all(projects)
        return True

def get_graph(name):
    p = get_project(name)
    return p.get("graph", {"nodes":[],"edges":[]}) if p else {"nodes":[],"edges":[]}

# ===== CHECKPOINTS =====
def save_checkpoint(name, step_data):
    path = os.path.join(CHECKPOINT_DIR, f"{name}.json")
    with _lock:
        cps = []
        if os.path.exists(path):
            with open(path) as f: cps = json.load(f)
        step_data["_ts"] = time.time()
        cps.append(step_data)
        with open(path, "w") as f: json.dump(cps, f, indent=2)
    return True

def get_checkpoints(name):
    path = os.path.join(CHECKPOINT_DIR, f"{name}.json")
    if not os.path.exists(path): return []
    with open(path) as f: return json.load(f)

def delete_checkpoints(name):
    path = os.path.join(CHECKPOINT_DIR, f"{name}.json")
    if os.path.exists(path): os.remove(path)

def restore_from_checkpoint(name, step_index):
    cps = get_checkpoints(name)
    if step_index >= len(cps): return None
    cp = cps[step_index]
    return cp

# ===== COST =====
# def estimate_cost(model, input_tokens, output_tokens):
#     return 0.0

# ===== HISTORY =====
def get_history(name):
    path = os.path.join(HISTORY_DIR, f"{name}.json")
    if not os.path.exists(path): return []
    with open(path) as f: return json.load(f)

def append_to_history(name, entry):
    path = os.path.join(HISTORY_DIR, f"{name}.json")
    with _lock:
        hist = []
        if os.path.exists(path):
            with open(path) as f: hist = json.load(f)
        hist.append(entry)
        while len(hist) > 100: hist.pop(0)
        with open(path, "w") as f: json.dump(hist, f, indent=2)
    return hist

def clear_history(name):
    path = os.path.join(HISTORY_DIR, f"{name}.json")
    if os.path.exists(path): os.remove(path)
