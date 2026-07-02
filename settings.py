import json, os, threading, copy, logging

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stormx_settings.json")
_lock = threading.RLock()
logger = logging.getLogger("stormx.settings")

DEFAULT_SETTINGS = {
    "assistant": {
        "api_key": "",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "moonshotai/kimi-k2.6:free",
        "system_prompt": "You are the Stormo AI assistant, an orchestrator of agents for video production.\nYou can:\n- List and describe projects\n- Modify agent configurations (prompt, model, temperature)\n- Create, rename, delete agents\n- Install skills on agents\n- Generate custom Python tools\n- Manage API credentials\n- Give advice on pipeline architecture\n\nRespond in English. If you take actions, use FUNCTION CALL.",
        "tools": [
            {"type":"function","function":{"name":"list_projects","description":"List all available projects","parameters":{"type":"object","properties":{}}}},
            {"type":"function","function":{"name":"get_project_config","description":"Get the full configuration of a project","parameters":{"type":"object","properties":{"project":{"type":"string"}},"required":["project"]}}},
            {"type":"function","function":{"name":"create_agent","description":"Create a new agent in a project","parameters":{"type":"object","properties":{"project":{"type":"string"},"agent":{"type":"string"},"prompt":{"type":"string"},"color":{"type":"string"}},"required":["project","agent","prompt"]}}},
            {"type":"function","function":{"name":"update_agent","description":"Update an agent's prompt, model, or temperature","parameters":{"type":"object","properties":{"project":{"type":"string"},"agent":{"type":"string"},"prompt":{"type":"string"},"model":{"type":"string"},"temperature":{"type":"number"}},"required":["project","agent"]}}},
            {"type":"function","function":{"name":"delete_agent","description":"Delete an agent from a project","parameters":{"type":"object","properties":{"project":{"type":"string"},"agent":{"type":"string"}},"required":["project","agent"]}}},
            {"type":"function","function":{"name":"connect_agents","description":"Connect two agents in the graph","parameters":{"type":"object","properties":{"project":{"type":"string"},"from":{"type":"string"},"to":{"type":"string"},"required_type":{"type":"string","enum":["optional","required"]}},"required":["project","from","to"]}}},
            {"type":"function","function":{"name":"disconnect_agents","description":"Remove a connection between two agents","parameters":{"type":"object","properties":{"project":{"type":"string"},"from":{"type":"string"},"to":{"type":"string"}},"required":["project","from","to"]}}},
            {"type":"function","function":{"name":"update_orchestrator","description":"Update the orchestrator settings","parameters":{"type":"object","properties":{"project":{"type":"string"},"model":{"type":"string"},"temperature":{"type":"number"}},"required":["project"]}}},
            {"type":"function","function":{"name":"install_skill","description":"Install a skill on an agent","parameters":{"type":"object","properties":{"project":{"type":"string"},"agent":{"type":"string"},"skill_id":{"type":"string"}},"required":["project","agent","skill_id"]}}},
        ]
    },
    "providers": [
        {"key":"openai","label":"OpenAI","url":"https://api.openai.com/v1","model":"gpt-4o"},
        {"key":"anthropic","label":"Anthropic (Claude)","url":"https://api.anthropic.com/v1","model":"claude-sonnet-4-20250514"},
        {"key":"google","label":"Google (Gemini)","url":"https://generativelanguage.googleapis.com/v1beta/openai","model":"gemini-2.0-flash"},
        {"key":"deepseek","label":"DeepSeek","url":"https://api.deepseek.com","model":"deepseek-chat"},
        {"key":"openrouter","label":"OpenRouter","url":"https://openrouter.ai/api/v1","model":"openrouter/auto"},
    ],
    "skills": [
        {"id":"scriptwriter","name":"Scriptwriter","category":"Writing","description":"Writes professional video scripts with narrative structure, dialogues and timing","prompt":"You are an experienced video production scriptwriter. Your task is to write professional video scripts.\n\nStructure:\n- Scenes and estimated duration\n- Dialogues and voiceover\n- Visual and directing notes\n- Tone and style consistent with the brief\n\nRequired output: complete script with numbered scenes, dialogues, directing notes and timing."},
        {"id":"storyboarder","name":"Storyboarder","category":"Production","description":"Creates detailed storyboards with shots, angles, transitions and timing","prompt":"You are a professional storyboarder. Your task is to create detailed storyboards for video productions.\n\nFor each scene:\n- Scene number and duration\n- Shot and angle\n- Visual description\n- Transitions\n- Audio/dialogue notes\n\nOutput: complete storyboard with all scenes numbered."},
        {"id":"voiceover","name":"Voiceover Artist","category":"Audio","description":"Writes voiceover scripts with pronunciation, tone and pause instructions","prompt":"You are a voiceover artist. Your task is to write professional voiceover scripts.\n\nGuidelines:\n- Short and flowing sentences\n- Pronunciation instructions for technical terms\n- Pauses and tone variations\n- Emotion and rhythm\n\nOutput: voiceover text with performance markings."},
        {"id":"captions","name":"Caption Generator","category":"Accessibility","description":"Generates SRT/VTT subtitles with synchronized timing","prompt":"You are a professional captioner. Generate subtitles in SRT format.\n\nRules:\n- Each subtitle: max 42 characters\n- Timing: HH:MM:SS,mmm format\n- Lip sync\n- Progressive numbering\n\nOutput: complete SRT file."},
        {"id":"musicsound","name":"Music & Sound","category":"Audio","description":"Recommends music, sound effects, BPM and royalty-free audio libraries","prompt":"You are a sound designer. Your task is to recommend the perfect soundtrack.\n\nFor each scene:\n- Recommended music genre\n- BPM and atmosphere\n- Sound effects\n- Suggested royalty-free libraries\n\nOutput: complete audio card for each scene."},
        {"id":"thumbnails","name":"Thumbnail Designer","category":"Design","description":"Generates visual briefs for thumbnails: composition, colors, psychology","prompt":"You are a thumbnail designer. Create visual briefs for engaging thumbnails.\n\nElements:\n- Composition and focal point\n- Color palette and psychology\n- Text and fonts\n- Attention-grabbing elements\n\nOutput: detailed thumbnail brief."},
        {"id":"ffmpeg","name":"FFmpeg Expert","category":"Technical","description":"Generates FFmpeg commands for editing, encoding, overlay and filters","prompt":"You are an FFmpeg expert. Generate ready-to-use commands.\n\nCategories:\n- Video cutting and merging\n- Encoding and compression\n- Overlay and filters\n- Audio extraction\n\nOutput: FFmpeg command with parameter explanation."},
        {"id":"publishing","name":"Publishing Manager","category":"Publishing","description":"Optimizes titles, descriptions, tags and metadata for each platform","prompt":"You are a publishing manager. Prepare content for publication.\n\nFor each platform:\n- SEO title\n- Optimized description\n- Relevant tags and hashtags\n- Chapters and cards\n\nOutput: multi-platform publishing card."},
        {"id":"translator","name":"Multilingual Translator","category":"Localization","description":"Translates scripts and video content into multiple languages while preserving tone and context","prompt":"You are a professional translator specialized in video content.\n\nRules:\n- Maintain the original tone and style\n- Adapt cultural references\n- Preserve timing for voiceover\n\nOutput: production-ready translation."},
        {"id":"revisor","name":"Quality Revisor","category":"Quality","description":"Analyzes and corrects scripts, subtitles and content for quality and consistency","prompt":"You are a quality reviewer. Analyze video content.\n\nChecklist:\n- Narrative consistency\n- Grammatical correctness\n- Completeness of information\n- Message clarity\n\nOutput: review report with corrections."},
        {"id":"seo","name":"SEO Specialist","category":"Marketing","description":"Researches keywords, analyzes competitors and optimizes content for ranking","prompt":"You are an SEO specialist for video content.\n\nActivities:\n- Main keyword research\n- Competitor analysis\n- Description optimization\n- Category suggestions\n\nOutput: complete SEO card."},
        {"id":"researcher","name":"Content Researcher","category":"Research","description":"Researches trends, data and references to enrich video content","prompt":"You are a content researcher. Enrich content with data and references.\n\nAreas:\n- Current industry trends\n- Relevant statistical data\n- Cultural references\n- Quotes and sources\n\nOutput: complete research dossier."},
    ],
    "templates": [
        {"id":"social-media-manager","name":"Social Media Manager","category":"Marketing","description":"Manages publication and optimization of content for social media.","color":"#a855f7","prompt":"You are a social media manager. Create content optimized for each social platform: format, tone, hashtags and publishing timing."},
        {"id":"technical-reviewer","name":"Technical Reviewer","category":"Quality","description":"Analyzes scripts and outputs for technical consistency and feasibility.","color":"#ef4444","prompt":"You are a technical reviewer. Analyze scripts and production plans to verify technical consistency and feasibility."},
        {"id":"creative-director","name":"Creative Director","category":"Creativity","description":"Creative supervision and feedback on concept and storytelling.","color":"#f59e0b","prompt":"You are a creative director. Provide feedback on concept, storytelling and artistic direction of the project."},
        {"id":"content-researcher","name":"Content Researcher","category":"Research","description":"Researches trends, competitors and references to enrich content.","color":"#06b6d4","prompt":"You are a content researcher. Search for trends, competitor analysis and cultural references to enrich video content."},
    ],
    "mcp_presets": [
        {"name":"notion","url":"https://api.notion.com/v1","description":"Notion - databases and pages"},
        {"name":"youtube","url":"https://www.googleapis.com/youtube/v3","description":"YouTube Data API - search videos, channels, playlists"},
        {"name":"google_gis","url":"https://www.googleapis.com/customsearch/v1","description":"Google Custom Search API - search web and images"},
        {"name":"openai","url":"https://api.openai.com/v1","description":"OpenAI API (chat, embeddings)"},
        {"name":"anthropic","url":"https://api.anthropic.com/v1","description":"Anthropic Claude API"},
        {"name":"google_ai","url":"https://generativelanguage.googleapis.com/v1beta","description":"Google Gemini API Key"},
        {"name":"github","url":"https://api.github.com","description":"GitHub API (repos, issues)"},
    ]
}

SEARCH_KEYS = {
    "skills": ["id", "name", "category"],
    "templates": ["id", "name", "category"],
    "providers": ["key", "label"],
    "mcp_presets": ["name"],
}

def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def _load():
    if not os.path.exists(SETTINGS_FILE):
        _save(copy.deepcopy(DEFAULT_SETTINGS))
        return copy.deepcopy(DEFAULT_SETTINGS)
    try:
        with _lock:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load settings: %s", e)
        bak = SETTINGS_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e2:
                logger.error("Failed to load settings backup: %s", e2)
        return copy.deepcopy(DEFAULT_SETTINGS)

def _save(data):
    with _lock:
        if os.path.exists(SETTINGS_FILE):
            try:
                import shutil
                shutil.copy2(SETTINGS_FILE, SETTINGS_FILE + ".bak")
            except OSError:
                pass
        _atomic_write(SETTINGS_FILE, data)

def get_all():
    return _load()

def get_section(section):
    s = _load()
    return s.get(section, copy.deepcopy(DEFAULT_SETTINGS.get(section, {})))

def update_section(section, data):
    with _lock:
        s = _load()
        s[section] = data
        _save(s)
    return True

def add_item(section, item, key_field="id"):
    with _lock:
        s = _load()
        items = s.setdefault(section, [])
        existing = next((x for x in items if x.get(key_field) == item.get(key_field)), None)
        if existing:
            existing.update(item)
        else:
            items.append(item)
        _save(s)
    return True

def remove_item(section, item_key, key_field="id"):
    with _lock:
        s = _load()
        items = s.get(section, [])
        s[section] = [x for x in items if x.get(key_field) != item_key]
        _save(s)
    return True

def get_env_overrides():
    s = _load()
    asst = s.get("assistant", {})
    asst["api_key"] = os.environ.get("ASSISTANT_API_KEY", asst.get("api_key", ""))
    asst["api_url"] = os.environ.get("ASSISTANT_API_URL", asst.get("api_url", "https://openrouter.ai/api/v1/chat/completions"))
    asst["model"] = os.environ.get("ASSISTANT_MODEL", asst.get("model", "moonshotai/kimi-k2.6:free"))
    return asst
