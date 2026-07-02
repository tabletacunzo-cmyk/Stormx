import json, asyncio, time, httpx, copy, os, re, shutil
from typing import AsyncGenerator, Optional
from channels import registry as channel_registry, TelegramChannel, DiscordChannel, WhatsAppChannel

_CHANNEL_REPLY_CHATS = {}

AGENT_EMOJIS = {
    "orchestrator": "\U0001F9E0", "scriptwriter": "\u270D\uFE0F",
    "storyboarder": "\U0001F3AC", "voiceover": "\U0001F399\uFE0F",
    "captions": "\U0001F5E8\uFE0F", "ffmpeg": "\U0001F3B5",
    "thumbnails": "\U0001F5BC\uFE0F", "musicsound": "\U0001F3B6",
    "publishing": "\U0001F4E2",
}

ORCHESTRATOR_PROMPT = (
    "You are the orchestrator of a swarm of specialized AI agents.\n\n"
    "PRIORITY RULES:\n"
    "1. IF the request is complex (video production, research, analysis, content, articulated projects) \u2192 you MUST ACTIVATE agents with ATTIVA: @agent at the end. DO NOT use TOOL_CALL.\n"
    "2. IF the request is ONLY a file operation (read, write, create, copy) \u2192 use TOOL_CALL: filesystem and then end with ATTIVA: nessuno.\n"
    "3. IF it's a simple question, greeting or generic chat \u2192 respond directly and write ATTIVA: nessuno.\n\n"
    "Available agents: {agent_list}\n\n"
    "At the end of your reasoning ALWAYS add ATTIVA: @agent1, @agent2 or ATTIVA: nessuno.\n"
    "If you don't specify ATTIVA, no agent will be called.\n\n"
    "Respond in English."
)


async def call_model_stream(
    api_key: str, api_url: str, model: str,
    system_prompt: str, user_message: str,
    temperature: float = 0.3,
) -> AsyncGenerator[str, None]:
    if not api_key:
        yield json.dumps({"type": "error", "content": "API key missing."}); return
    if not api_url:
        yield json.dumps({"type": "error", "content": "API URL missing."}); return
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model, "temperature": temperature, "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", f"{api_url.rstrip('/')}/chat/completions", json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield json.dumps({"type": "error", "content": f"API Error ({resp.status_code}): {body.decode('utf-8', 'replace')[:600]}"})
                    return
                async for line in resp.aiter_lines():
                    if not line: continue
                    if line.startswith("data: "):
                        raw = line[6:].strip()
                        if raw == "[DONE]": break
                        try:
                            chunk = json.loads(raw)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield json.dumps({"type": "chunk", "content": content})
                            if chunk.get("usage") or chunk.get("usage_metadata"):
                                usage = chunk.get("usage") or chunk.get("usage_metadata")
                                yield json.dumps({
                                    "type": "usage",
                                    "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
                                    "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
                                })
                        except json.JSONDecodeError:
                            continue
    except httpx.ConnectError:
        yield json.dumps({"type": "error", "content": f"Connection refused to {api_url}."})
    except httpx.TimeoutException:
        yield json.dumps({"type": "error", "content": "Timeout."})
    except Exception as e:
        yield json.dumps({"type": "error", "content": f"{type(e).__name__}: {str(e)}"})


async def _resolve_channel(project: str, ch_node_id: str,
                           ch_config: dict, channels_config: dict):
    ch_type = ch_config.get("type", "")
    cfg = ch_config.get("config", {})
    if ch_type == "telegram":
        token = channel_registry.resolve_bot_token(project, cfg)
        if not token:
            return None, "(ERROR: bot_token not configured for Telegram)"
        tg = channel_registry.get_telegram(token)
        if not tg:
            tg = channel_registry.register_telegram(token, project, ch_node_id)
        msgs = await tg.listen_once()
        if not msgs:
            return None, "(No new messages on Telegram)"
        reply_chat = msgs[-1]["chat_id"]
        _CHANNEL_REPLY_CHATS[ch_node_id] = reply_chat
        lines = [f"  - From: {m['from']} | Chat: {m['chat_id']}\n    Text: {m['text']}" for m in msgs]
        return reply_chat, "Messages received from Telegram:\n" + "\n".join(lines)
    elif ch_type == "discord":
        token = cfg.get("bot_token", "")
        cid = cfg.get("channel_id", "")
        if not token or not cid:
            return None, "(ERROR: bot_token and channel_id required for Discord)"
        dc = channel_registry.register_discord(token, cid, project, ch_node_id)
        msgs = await dc.get_messages()
        if not msgs:
            return None, "(No recent messages on Discord)"
        _CHANNEL_REPLY_CHATS[ch_node_id] = cid
        lines = [f"  - {m['from']}: {m['text']}" for m in msgs]
        return cid, f"Recent messages from Discord:\n" + "\n".join(lines)
    elif ch_type == "whatsapp":
        return None, "(WhatsApp: messages are received via webhook.)"
    return None, ""


async def _send_channel_reply(project: str, ch_node_id: str,
                              ch_config: dict, text: str):
    ch_type = ch_config.get("type", "")
    cfg = ch_config.get("config", {})
    if not text:
        return
    if ch_type == "telegram":
        token = channel_registry.resolve_bot_token(project, cfg)
        if not token:
            return
        tg = channel_registry.get_telegram(token)
        if not tg:
            tg = channel_registry.register_telegram(token, project, ch_node_id)
        reply_chat = _CHANNEL_REPLY_CHATS.get(ch_node_id, cfg.get("channel_id", ""))
        await tg.send_message(reply_chat, text) if reply_chat else None
    elif ch_type == "discord":
        token = cfg.get("bot_token", "")
        cid = _CHANNEL_REPLY_CHATS.get(ch_node_id, cfg.get("channel_id", ""))
        if token and cid:
            dc = channel_registry.register_discord(token, cid, project, ch_node_id)
            await dc.send_message(text)
    elif ch_type == "whatsapp":
        token = cfg.get("api_token", "")
        pid = cfg.get("phone_number_id", "")
        if token and pid:
            wa = channel_registry.register_whatsapp(token, pid, project, ch_node_id)
            to = cfg.get("channel_id", "")
            if to:
                await wa.send_message(to, text)


async def run_pipeline_graph(
    brief: str, graph: dict, orchestrator_config: dict, agents_config: dict,
    channels_config: dict = {}, history: list = None,
    channel_trigger: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    history = history or []
    ok = orchestrator_config.get("api_key", "")
    ou = orchestrator_config.get("api_url", "")
    if not ok or not ou:
        yield json.dumps({"type": "error", "content": "Provider not configured. Open the orchestrator settings (double-click on the node) and configure at least one provider with API key and URL."})
        return
    project = orchestrator_config.get("_project", "default")
    _current_project_for_tools = project
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    successors = {}
    predecessors = {}
    for e in edges:
        successors.setdefault(e["from"], []).append(e["to"])
        predecessors.setdefault(e["to"], []).append({"from": e["from"], "type": e.get("type", "optional")})

    enabled_agents = [n["id"] for n in nodes.values()
                      if n["type"] == "agent" and agents_config.get(n["id"], {}).get("enabled", True)]
    channel_nodes = [n["id"] for n in nodes.values() if n["type"] == "channel"]
    agent_list_str = ", ".join(f"@{a}" for a in enabled_agents)
    yield json.dumps({"type": "orchestrator_start", "agents": enabled_agents})

    order = []
    visited = set()
    queue_list = ["orchestrator"]
    for ch in channel_nodes:
        has_incoming = any(e["to"] == ch for e in edges)
        if not has_incoming and ch not in queue_list:
            queue_list.append(ch)
    while queue_list:
        curr = queue_list.pop(0)
        if curr in visited:
            continue
        visited.add(curr)
        order.append(curr)
        for nxt in successors.get(curr, []):
            if nxt not in visited and nxt not in queue_list:
                queue_list.append(nxt)
    order = [n for n in order if n == "orchestrator" or n in enabled_agents or n in channel_nodes]

    yield json.dumps({"type": "graph_clear"})
    yield json.dumps({"type": "graph_order", "order": order})

    pipeline_context = f"Original brief: {brief}\n\n"
    outputs = {}
    total_cost = 0.0
    pipeline_t0 = time.time()
    broadcast_mode = orchestrator_config.get("broadcast", False)
    broadcast_consumed = False

    async def _run_agent_to_queue(queue: asyncio.Queue, agent_id: str, cfg: dict,
                                  sys_prompt: str, user_msg: str) -> dict:
        parts, saw_error = [], False
        t0 = time.time()
        usage_in, usage_out = 0, 0
        try:
            async for chunk in call_model_stream(
                cfg["api_key"] if agent_id == "orchestrator" else (cfg.get("api_key") or orchestrator_config["api_key"]),
                cfg["api_url"] if agent_id == "orchestrator" else (cfg.get("api_url") or orchestrator_config["api_url"]),
                cfg["model"] if agent_id == "orchestrator" else (cfg.get("model") or orchestrator_config["model"]),
                sys_prompt, user_msg,
                cfg.get("temperature", orchestrator_config.get("temperature", 0.3)),
            ):
                data = json.loads(chunk)
                if data["type"] == "chunk":
                    parts.append(data["content"])
                    await queue.put(("chunk", agent_id, data["content"]))
                elif data["type"] == "error":
                    await queue.put(("error", agent_id, data["content"]))
                    saw_error = True
                    break
                elif data["type"] == "usage":
                    usage_in = data.get("input_tokens", usage_in)
                    usage_out = data.get("output_tokens", usage_out)
        except Exception as e:
            await queue.put(("error", agent_id, f"ERROR: {type(e).__name__}: {str(e)}"))
            saw_error = True
            parts.append(f"ERROR: {type(e).__name__}: {str(e)}")
        elapsed = round(time.time() - t0, 1)
        full_output = "".join(parts) or "(No output)"
        if usage_in and usage_out:
            input_tokens, output_tokens = usage_in, usage_out
        else:
            input_tokens, output_tokens = len(sys_prompt + user_msg) / 4, len(full_output) / 4
        model_used = cfg.get("model") or orchestrator_config["model"]
        step_cost = 0.0
        await queue.put(None)
        return {
            "id": agent_id, "output": full_output, "saw_error": saw_error,
            "elapsed": elapsed, "tokens": round(input_tokens + output_tokens), "cost": round(step_cost, 6),
        }

    def _build_sys_prompt(is_orch, cfg):
        ork_sys = orchestrator_config.get("system_prompt", "").strip()
        if is_orch:
            sys = ork_sys if ork_sys else ORCHESTRATOR_PROMPT.format(agent_list=agent_list_str)
            if "filesystem" in MCP_REGISTRY:
                sys += "\n\nYou have the filesystem tool available for file operations (only when explicitly requested).\n"
                sys += f"filesystem: {MCP_REGISTRY['filesystem']['description']}\n"
                sys += "\nTOOL_CALL format:\n"
                sys += 'TOOL_CALL: filesystem {"op": "write", "path": "C:/path/file.txt", "content": "text"}\n'
                sys += 'TOOL_CALL: filesystem {"op": "read", "path": "C:/path/file.txt"}\n'
                sys += 'TOOL_CALL: filesystem {"op": "mkdir", "path": "C:/path/new_folder"}\n'
                sys += 'TOOL_CALL: filesystem {"op": "list", "path": "C:/path"}\n'
                sys += 'TOOL_CALL: filesystem {"op": "delete", "path": "C:/file.txt"}\n'
                sys += 'TOOL_CALL: filesystem {"op": "copy", "path": "C:/source.txt", "dest": "C:/destination.txt"}\n\n'
                sys += "RULE: IF the user asks for a file operation, use TOOL_CALL: filesystem immediately.\n"
                sys += "IF the request is complex (video production, research, analysis), DO NOT use filesystem, but ACTIVATE agents with ATTIVA: @agent."
            if channel_nodes:
                ch_list = ", ".join(f"@{c}" for c in channel_nodes)
                sys += f"\n\nYou have the following messaging channels available: {ch_list}.\n"
                sys += "You can send messages to a channel with TOOL_CALL: send_to_channel {\"channel\": \"@channel_name\", \"message\": \"text\"}\n"
                sys += "Use send_to_channel to reply directly to the user on the channel from which the message arrived.\n"
            return sys
        sys = cfg.get("prompt", "")
        all_tools = list(cfg.get("tools") or []) + list(cfg.get("mcp_tools") or [])
        tool_names = {t["name"] for t in all_tools}
        if "filesystem" not in tool_names and "filesystem" in MCP_REGISTRY:
            all_tools.append({"name": "filesystem", "description": MCP_REGISTRY["filesystem"]["description"]})
        if all_tools:
            sys += "\n\nYou have these tools available:\n"
            for t in all_tools:
                desc = t.get("description", "")
                if not desc and t["name"] in MCP_REGISTRY:
                    desc = MCP_REGISTRY[t["name"]].get("description", "")
                sys += f"\n- {t['name']}: {desc}"
            sys += "\n\nTOOL USAGE:\n"
            sys += "Call a tool ONLY if the task requires external data (search YouTube, Notion, web, etc.).\n"
            sys += "If the request is a simple presentation, question or basic operation, respond DIRECTLY without tools.\n"
            sys += "Tool format:\n"
            sys += 'TOOL_CALL: tool_name {"endpoint": "/path", "key": "value"}\n'
            sys += 'Examples:\n'
            sys += 'TOOL_CALL: notion {"endpoint": "/search", "query": "test"}\n'
            sys += 'TOOL_CALL: youtube {"endpoint": "/search", "part": "snippet", "q": "video"}\n'
            sys += 'After TOOL_CALL the real result arrives. Analyze it and decide if other tools are needed.\n'
            sys += 'If the tool gives an error, try different parameters.\n'
            sys += 'Once you have all the data, provide the final answer WITHOUT TOOL_CALL.'
            for _t in all_tools:
                tn = _t.get("name", "").lower()
                if "youtube" in tn:
                    sys += '\nYouTube tools ALWAYS require part=snippet. NEVER use type=video (causes 403 error). To search for a channel use type=channel.'
            sys += "\n"
        rag = cfg.get("rag_config", {})
        if rag.get("enabled") and brief:
            try:
                from rag import query_context as _rag_context
                context = _rag_context(project, brief[:500], top_k=5)
                if context:
                    sys += "\n\n=== RAG CONTEXT ===\n" + context + "\n=== END RAG CONTEXT ==="
            except Exception:
                pass
        return sys

    def _save_chk(step_idx, node_id, full_output, sys_prompt, cfg, step_cost, tokens, elapsed):
        from config import save_checkpoint
        save_checkpoint(node_id + "_" + str(step_idx), {
            "step": step_idx, "node": node_id, "output": full_output,
            "prompt_snapshot": sys_prompt, "config_snapshot": dict(cfg),
            "cost": round(step_cost, 6), "tokens": round(tokens), "time": elapsed,
            "brief": brief, "pipeline_time": round(time.time() - pipeline_t0, 1),
        })

    loop_config = orchestrator_config.get("loop_config", {})
    max_iterations = int(loop_config.get("max_iterations", 3))
    loop_enabled = loop_config.get("enabled", False)
    loopback_edges = [e for e in edges if e.get("loop", False)]

    async def _check_fermati(output_text):
        lower = output_text.lower()[-500:]
        return any(w in lower for w in ["stop", "finito", "completato", "goal reached", "risposta finale"])

    def _parse_agent_selection(text):
        import re as _re
        m = _re.search(r'(?:ATTIVA|ACTIVATE)\s*:\s*(.+)', text, _re.IGNORECASE | _re.DOTALL)
        if not m:
            return None
        raw = m.group(1).strip().lower()
        if raw in ("nessuno", "none", "nessuno."):
            return []
        selected = []
        for part in _re.split(r'[,;]\s*', raw):
            part = part.strip()
            if part.startswith('@'):
                selected.append(part[1:].strip('* '))
        return selected if selected else None

    selected_agents = None

    # --- Phase 0: Pre-read from channels (skip if triggered by channel) ---
    if not channel_trigger:
        for ch_id in channel_nodes:
            ch = channels_config.get(ch_id)
            if not ch or not ch.get("enabled", True):
                continue
            reply_chat, result_text = await _resolve_channel(project, ch_id, ch, channels_config)
            if result_text and "ERROR" not in result_text and "(No " not in result_text:
                outputs[ch_id] = result_text
                pipeline_context += f"\n=== Messages from channel @{ch_id} ===\n{result_text}\n"
                yield json.dumps({"type": "channel_output", "node": ch_id, "content": result_text})

    # If triggered by channel, add trigger context
    if channel_trigger:
        trigger_text = channel_trigger.get("text", "")
        trigger_chat = channel_trigger.get("chat_id", "")
        trigger_node = channel_trigger.get("channel_node_id", "")
        if trigger_text:
            _CHANNEL_REPLY_CHATS[trigger_node] = trigger_chat
            ctx = f"\n=== Message from channel @{trigger_node} ===\nFrom: {trigger_chat}\nText: {trigger_text}\n"
            pipeline_context += ctx
            if trigger_node in channel_nodes and trigger_node not in outputs:
                outputs[trigger_node] = trigger_text
                yield json.dumps({"type": "channel_output", "node": trigger_node, "content": f"Message received: {trigger_text}"})

    # --- Run the pipeline ---
    for iteration in range(max_iterations if loop_enabled else 1):
        if iteration > 0:
            yield json.dumps({"type": "loop_iteration", "iteration": iteration, "max": max_iterations})
            brief = brief + f"\n\n=== ITERATION {iteration} ===\nAccumulated context:\n{pipeline_context}"

        for step_idx, node_id in enumerate(order):
            is_orch = node_id == "orchestrator"
            actual_id = node_id
            if node_id in channel_nodes:
                yield json.dumps({"type": "graph_node_active", "node": actual_id})
                yield json.dumps({"type": "graph_node_done", "node": actual_id})
                continue
            if not is_orch and broadcast_consumed:
                yield json.dumps({"type": "graph_node_skip", "node": actual_id})
                continue
            if not is_orch and selected_agents is not None and node_id not in selected_agents:
                required_from = [p["from"] for p in predecessors.get(node_id, []) if p["type"] == "required"]
                if not required_from:
                    yield json.dumps({"type": "graph_node_skip", "node": actual_id})
                    continue
            cfg = orchestrator_config if is_orch else agents_config.get(node_id, {})
            yield json.dumps({"type": "graph_node_active", "node": actual_id})

            if is_orch:
                channel_data = ""
                for ch_id in channel_nodes:
                    if ch_id in outputs:
                        channel_data += f"\nData from channel @{ch_id}:\n{outputs[ch_id]}\n"
                if iteration == 0:
                    history_text = ""
                    if history:
                        history_text = "\n\n=== CONVERSATION HISTORY ===\n"
                        for h in history:
                            role = "User" if h.get("role") == "user" else "Assistant"
                            history_text += f"\n{role}: {h.get('content', '')[:1000]}\n"
                        history_text += "\n=== END HISTORY ===\n"
                    channel_info = ""
                    if channel_nodes:
                        channel_info = "\nNote: there are messaging channels (@" + ", @".join(channel_nodes) + ") connected. They can READ messages from external chats (already included above) and SEND the final response. Consider the read messages for your response.\n"
                    user_msg = f"Analyze this brief and produce a detailed plan.\nAvailable agents: {agent_list_str}\nDecide WHICH agents are needed and write ATTIVA: @agent1, @agent2 at the end.\n{channel_info}\n\nBrief:\n{brief}\n{channel_data}{history_text}"
                else:
                    user_msg = (
                        f"Original brief: {brief.split('=== ITERATION')[0].strip()}\n\n"
                        f"Outputs received from agents so far:\n{pipeline_context[-3000:]}\n\n"
                        f"Evaluate whether the brief has been satisfied.\n"
                        f"If the work is complete, provide the FINAL ANSWER and end with the word STOP.\n"
                        f"If more work is needed, explain what's missing and end with the word CONTINUE."
                    )
            else:
                orch_out = outputs.get("orchestrator", "")
                user_msg = orch_out.replace("ATTIVA:", "").replace("attiva:", "").strip()
                if not user_msg:
                    user_msg = pipeline_context
                required_from = [p["from"] for p in predecessors.get(node_id, []) if p["type"] == "required"]
                if required_from:
                    for src in required_from:
                        out = outputs.get(src)
                        if out:
                            user_msg += f"\n\n=== Input OBBLIGATORIO da @{src} ===\n{out}\n"

            sys_prompt = _build_sys_prompt(is_orch, cfg)
            yield json.dumps({"type": "agent_start", "agent": actual_id, "color": cfg.get("color", "#888"), "emoji": AGENT_EMOJIS.get(actual_id, "\U0001F916")})

            if is_orch or (not broadcast_mode):
                parts, saw_error = [], False
                t0 = time.time()
                MAX_TOOL_CALLS = 5
                current_user_msg = user_msg
                last_tool_result = ""
                total_usage_in, total_usage_out = 0, 0
                for tool_turn in range(MAX_TOOL_CALLS + 1):
                    turn_parts = []
                    turn_t0 = time.time()
                    turn_usage_in, turn_usage_out = 0, 0
                    async for chunk in call_model_stream(
                        cfg["api_key"] if is_orch else (cfg.get("api_key") or orchestrator_config["api_key"]),
                        cfg["api_url"] if is_orch else (cfg.get("api_url") or orchestrator_config["api_url"]),
                        cfg["model"] if is_orch else (cfg.get("model") or orchestrator_config["model"]),
                        sys_prompt, current_user_msg,
                        cfg.get("temperature", orchestrator_config.get("temperature", 0.3)),
                    ):
                        data = json.loads(chunk)
                        if data["type"] == "chunk":
                            turn_parts.append(data["content"])
                            yield json.dumps({"type": "agent_chunk", "agent": actual_id, "content": data["content"]})
                        elif data["type"] == "error":
                            yield json.dumps({"type": "agent_error", "agent": actual_id, "content": data["content"]})
                            yield json.dumps({"type": "graph_node_error", "node": actual_id})
                            saw_error = True
                            break
                        elif data["type"] == "usage":
                            turn_usage_in = data.get("input_tokens", turn_usage_in)
                            turn_usage_out = data.get("output_tokens", turn_usage_out)
                    if saw_error:
                        break
                    turn_output = "".join(turn_parts)
                    parts.append(turn_output)
                    total_usage_in += turn_usage_in
                    total_usage_out += turn_usage_out
                    tcs = _parse_tool_calls(turn_output)
                    if not tcs or tool_turn >= MAX_TOOL_CALLS - 1:
                        break
                    for tc_entry in tcs:
                        tool_name = tc_entry["tool"]
                        tool_params = tc_entry["params"]
                        tool_t0 = time.time()
                        yield json.dumps({"type": "tool_execute", "agent": actual_id, "tool": tool_name, "params": tool_params, "ts": tool_t0})
                        normal_tools = cfg.get("tools") or []
                        normal_tool = next((t for t in normal_tools if t["name"] == tool_name), None)
                        if normal_tool:
                            tool_result = await execute_tool(normal_tool.get("code", ""), tool_params)
                        else:
                            tool_result = await call_mcp_tool(tool_name, tool_params)
                        tool_elapsed = round(time.time() - tool_t0, 3)
                        yield json.dumps({"type": "tool_result", "agent": actual_id, "tool": tool_name, "result": tool_result[:500], "timing": tool_elapsed})
                        last_tool_result = tool_result[:2000]
                        current_user_msg += f"\n[Tool '{tool_name}' executed. Result: {last_tool_result}]\n"

                full_output = "".join(parts) or "(No output)"
                if last_tool_result:
                    full_output += f"\n[Tool result: {last_tool_result[:1000]}]"
                elapsed = round(time.time() - t0, 1)
                model_used = cfg.get("model") or orchestrator_config["model"]
                if total_usage_in and total_usage_out:
                    input_tokens, output_tokens = total_usage_in, total_usage_out
                else:
                    input_tokens, output_tokens = len(sys_prompt + current_user_msg) / 4, len(full_output) / 4
                tokens = input_tokens + output_tokens
                step_cost = 0.0
                total_cost += step_cost
                _save_chk(step_idx, actual_id, full_output, sys_prompt, cfg, step_cost, tokens, elapsed)

                if saw_error:
                    yield json.dumps({"type": "pipeline_failed", "step": step_idx, "node": actual_id, "error": last_tool_result or full_output})
                    yield json.dumps({"type": "graph_node_error", "node": actual_id})
                    return

                outputs[actual_id] = full_output
                yield json.dumps({"type": "agent_done", "agent": actual_id, "content": full_output, "timing": elapsed, "cost": round(step_cost, 6), "tokens": round(tokens)})
                yield json.dumps({"type": "graph_node_done", "node": actual_id})
                pipeline_context += f"\n=== Output from @{actual_id} ===\n{full_output}\n"

                if is_orch:
                    sel = _parse_agent_selection(full_output)
                    if sel is not None:
                        if not sel:
                            selected_agents = []
                        else:
                            valid = [s for s in sel if s in order]
                            if not valid:
                                sel = None
                            else:
                                selected_agents = sel
                        yield json.dumps({"type": "orchestrator_activate", "agents": sel or []})

                if not is_orch:
                    ag_loop = cfg.get("loop_config", {})
                    if ag_loop.get("enabled", False):
                        ag_max = int(ag_loop.get("max_iterations", 5))
                        if await _check_fermati(full_output):
                            yield json.dumps({"type": "loop_early_exit", "agent": actual_id, "reason": "Goal already reached"})
                        else:
                            for li in range(ag_max):
                                if li > 0 and await _check_fermati(outputs[actual_id]):
                                    yield json.dumps({"type": "loop_early_exit", "agent": actual_id, "reason": "Goal reached"})
                                    break
                                lb_user = (f"Auto-loop {li+1}. {'Continue your work.' if li > 0 else 'First iteration.'}"
                                           f"\n\nYour previous output:\n{full_output if li == 0 else outputs[actual_id][-2000:]}\n\n{pipeline_context[-2000:]}"
                                           f"\n\nWhen you're done, end with STOP. If you need another iteration, end with CONTINUE.")
                                yield json.dumps({"type": "agent_start", "agent": actual_id, "color": cfg.get("color", "#888"), "emoji": AGENT_EMOJIS.get(actual_id, "\U0001F916"), "loop": True})
                                parts2, err2 = [], False
                                t0_2 = time.time()
                                async for chunk in call_model_stream(
                                    cfg.get("api_key") or orchestrator_config["api_key"],
                                    cfg.get("api_url") or orchestrator_config["api_url"],
                                    cfg.get("model") or orchestrator_config["model"],
                                    sys_prompt, lb_user,
                                    cfg.get("temperature", orchestrator_config.get("temperature", 0.3)),
                                ):
                                    d2 = json.loads(chunk)
                                    if d2["type"] == "chunk":
                                        parts2.append(d2["content"])
                                        yield json.dumps({"type": "agent_chunk", "agent": actual_id, "content": d2["content"]})
                                    elif d2["type"] == "error":
                                        yield json.dumps({"type": "agent_error", "agent": actual_id, "content": d2["content"]})
                                        err2 = True; break
                                lb_out = "".join(parts2) or "(No output)"
                                outputs[actual_id] = lb_out
                                pipeline_context += f"\n=== Auto-loop {li+1} @{actual_id} ===\n{lb_out}\n"
                                yield json.dumps({"type": "agent_done", "agent": actual_id, "content": lb_out, "loop": True})
                                if err2: break
            else:
                queue = asyncio.Queue()
                tasks = []
                broadcast_agents = [a for a in enabled_agents if selected_agents is None or a in selected_agents]
                for aid in broadcast_agents:
                    ac = agents_config.get(aid, {})
                    yield json.dumps({"type": "agent_start", "agent": aid, "color": ac.get("color", "#888"), "emoji": AGENT_EMOJIS.get(aid, "\U0001F916")})
                orch_out = outputs.get("orchestrator", "")
                broadcast_base = orch_out.replace("ATTIVA:", "").replace("attiva:", "").strip()
                if not broadcast_base:
                    broadcast_base = pipeline_context
                for aid in broadcast_agents:
                    acfg = agents_config.get(aid, {})
                    a_sys = _build_sys_prompt(False, acfg)
                    tasks.append(asyncio.create_task(_run_agent_to_queue(queue, aid, acfg, a_sys, broadcast_base)))
                remaining = len(tasks)
                while remaining > 0:
                    item = await queue.get()
                    if item is None:
                        remaining -= 1
                        continue
                    kind = item[0]
                    if kind == "chunk":
                        _, aid, content = item
                        yield json.dumps({"type": "agent_chunk", "agent": aid, "content": content})
                    elif kind == "error":
                        _, aid, content = item
                        yield json.dumps({"type": "agent_error", "agent": aid, "content": content})
                        yield json.dumps({"type": "graph_node_error", "node": aid})
                agent_results = await asyncio.gather(*tasks)
                for res in agent_results:
                    total_cost += res["cost"]
                    outputs[res["id"]] = res["output"]
                    _save_chk(step_idx, res["id"], res["output"], "", agents_config.get(res["id"], {}), res["cost"], res["tokens"], res["elapsed"])
                    if res["saw_error"]:
                        yield json.dumps({"type": "graph_node_error", "node": res["id"]})
                    yield json.dumps({"type": "agent_done", "agent": res["id"], "content": res["output"], "timing": res["elapsed"], "cost": res["cost"], "tokens": res["tokens"]})
                    yield json.dumps({"type": "graph_node_done", "node": res["id"]})
                    pipeline_context += f"\n=== Output from @{res['id']} ===\n{res['output']}\n"
                broadcast_consumed = True

        for lb_edge in loopback_edges:
            src = lb_edge["from"]
            dst = lb_edge["to"]
            if src in outputs and dst in enabled_agents:
                loop_max = int(lb_edge.get("loop_max", 3))
                for li in range(loop_max):
                    if li > 0 and await _check_fermati(outputs.get(dst, "")):
                        yield json.dumps({"type": "loop_early_exit", "agent": dst, "reason": "Goal reached"})
                        break
                    lb_user = f"Loop {li+1}/{loop_max}: @{src} produced:\n{outputs[src]}\n\n{pipeline_context}"
                    lb_cfg = agents_config.get(dst, {})
                    lb_sys = _build_sys_prompt(False, lb_cfg)
                    yield json.dumps({"type": "agent_start", "agent": dst, "color": lb_cfg.get("color", "#888"), "emoji": AGENT_EMOJIS.get(dst, "\U0001F916"), "loop": True})
                    parts, saw_error = [], False
                    t0 = time.time()
                    async for chunk in call_model_stream(
                        lb_cfg.get("api_key") or orchestrator_config["api_key"],
                        lb_cfg.get("api_url") or orchestrator_config["api_url"],
                        lb_cfg.get("model") or orchestrator_config["model"],
                        lb_sys, lb_user,
                        lb_cfg.get("temperature", orchestrator_config.get("temperature", 0.3)),
                    ):
                        data = json.loads(chunk)
                        if data["type"] == "chunk":
                            parts.append(data["content"])
                            yield json.dumps({"type": "agent_chunk", "agent": dst, "content": data["content"]})
                        elif data["type"] == "error":
                            yield json.dumps({"type": "agent_error", "agent": dst, "content": data["content"]})
                            saw_error = True; break
                    lb_out = "".join(parts) or "(No output)"
                    outputs[dst] = lb_out
                    pipeline_context += f"\n=== Loop {li+1}/{loop_max} @{dst} ===\n{lb_out}\n"
                    yield json.dumps({"type": "agent_done", "agent": dst, "content": lb_out, "loop": True})
                    if saw_error: break

        if loop_enabled and "orchestrator" in outputs and iteration < max_iterations - 1:
            if await _check_fermati(outputs["orchestrator"]):
                yield json.dumps({"type": "loop_early_exit", "agent": "orchestrator", "reason": "Goal reached"})
                break

    # --- Final orchestrator response ---
    final_sys = _build_sys_prompt(True, orchestrator_config)
    if selected_agents is not None and len(selected_agents) == 0:
        final_output = pipeline_context.strip() or "(No response)"
        import re as _re
        for pat in [r'(?i)TOOL_CALL\s*:\s*\S+\s*\{.*?\}', r'(?i)\bATTIVA\s*:.*', r'\[Tool result:.*?\]', r'\[Tool .*? executed\..*?\]']:
            final_output = _re.sub(pat, '', final_output)
        final_output = _re.sub(r'\n{3,}', '\n\n', final_output).strip()
        final_output = final_output or "(No response)"
        yield json.dumps({"type": "agent_start", "agent": "orchestrator", "color": orchestrator_config.get("color", "#888"), "emoji": AGENT_EMOJIS.get("orchestrator", "\U0001F9E0"), "final": True})
        yield json.dumps({"type": "agent_chunk", "agent": "orchestrator", "content": final_output, "final": True})
        yield json.dumps({"type": "agent_done", "agent": "orchestrator", "content": final_output, "final": True, "timing": 0})
    else:
        final_user = (
            f"Original brief: {brief}\n\n"
            f"Here are all the outputs produced by the agents:\n{pipeline_context}\n\n"
            f"Respond directly to the user based on these contributions. "
            f"Provide a clear and complete answer, as if you were speaking to the user."
        )
        yield json.dumps({"type": "agent_start", "agent": "orchestrator", "color": orchestrator_config.get("color", "#888"), "emoji": AGENT_EMOJIS.get("orchestrator", "\U0001F9E0"), "final": True})
        final_parts, final_err = [], False
        final_t0, final_usage_in, final_usage_out = time.time(), 0, 0
        async for chunk in call_model_stream(
            orchestrator_config["api_key"],
            orchestrator_config["api_url"],
            orchestrator_config["model"],
            final_sys, final_user,
            orchestrator_config.get("temperature", 0.3),
        ):
            fd = json.loads(chunk)
            if fd["type"] == "chunk":
                final_parts.append(fd["content"])
                yield json.dumps({"type": "agent_chunk", "agent": "orchestrator", "content": fd["content"], "final": True})
            elif fd["type"] == "error":
                yield json.dumps({"type": "agent_error", "agent": "orchestrator", "content": fd["content"]})
                final_err = True; break
            elif fd["type"] == "usage":
                final_usage_in = fd.get("input_tokens", final_usage_in)
                final_usage_out = fd.get("output_tokens", final_usage_out)
        final_output = "".join(final_parts) or "(No final response)"
        if final_usage_in and final_usage_out:
            final_step_cost = 0.0
            total_cost += final_step_cost
        yield json.dumps({"type": "agent_done", "agent": "orchestrator", "content": final_output, "final": True, "timing": round(time.time() - final_t0, 1)})

    # --- Phase Final: Send response through channels ---
    response_to_send = final_output.strip() if final_output and final_output != "(No final response)" else pipeline_context.strip()
    if response_to_send:
        for ch_id in channel_nodes:
            ch = channels_config.get(ch_id)
            if not ch or not ch.get("enabled", True):
                continue
            yield json.dumps({"type": "channel_output", "node": ch_id, "content": f"Sending response to {ch.get('type', 'channel')}..."})
            try:
                await _send_channel_reply(project, ch_id, ch, response_to_send[:2000])
            except Exception as e:
                yield json.dumps({"type": "channel_output", "node": ch_id, "content": f"Send ERROR: {e}"})

    yield json.dumps({"type": "pipeline_done", "timing": round(time.time() - pipeline_t0, 1), "total_cost": round(total_cost, 6)})
    yield json.dumps({"type": "graph_clear"})


async def run_pipeline(brief, orchestrator_config, agents_config, channels_config={}):
    graph = {"nodes": [{"id": "orchestrator", "type": "orchestrator"}], "edges": []}
    for name in agents_config:
        if agents_config[name].get("enabled", True):
            graph["nodes"].append({"id": name, "type": "agent"})
            graph["edges"].append({"from": "orchestrator", "to": name})
    async for ev in run_pipeline_graph(brief, graph, orchestrator_config, agents_config, channels_config):
        yield ev

external_call_log = []


def log_external_call(method, url, status, payload=""):
    import time
    external_call_log.append({"ts": time.time(), "method": method, "url": str(url)[:200], "status": status, "payload": str(payload)[:200]})
    while len(external_call_log) > 200:
        external_call_log.pop(0)


def _parse_tool_calls(text: str) -> list:
    results = []
    idx = 0
    while True:
        marker = "TOOL_CALL:"
        pos = text.find(marker, idx)
        if pos == -1:
            break
        rest = text[pos + len(marker):].lstrip()
        parts = rest.split(None, 1)
        if not parts:
            break
        tool_name = parts[0]
        raw = (parts[1] if len(parts) > 1 else "").strip()
        params_str = raw
        if raw.startswith('{'):
            depth, i = 0, 0
            for ch in raw:
                i += 1
                if ch == '{': depth += 1
                elif ch == '}': depth -= 1
                if depth == 0: break
            params_str = raw[:i]
            idx = pos + len(marker) + len(rest) - len(raw) + i
        elif raw.startswith('['):
            depth, i = 0, 0
            for ch in raw:
                i += 1
                if ch == '[': depth += 1
                elif ch == ']': depth -= 1
                if depth == 0: break
            params_str = raw[:i]
            idx = pos + len(marker) + len(rest) - len(raw) + i
        else:
            idx = pos + len(marker) + len(parts[0]) + 1
        try:
            params = json.loads(params_str) if params_str.startswith(('{', '[')) else {"input": params_str}
        except json.JSONDecodeError:
            params = {"input": params_str}
        results.append({"tool": tool_name, "params": params})
    return results

MCP_REGISTRY = {}


def register_mcp_tool(name, url, description, func=None, api_key=""):
    MCP_REGISTRY[name] = {"url": url, "description": description, "func": func, "api_key": api_key}


async def execute_tool(tool_code: str, params: dict, timeout: int = 15) -> str:
    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(None, lambda: _run_tool_sync(tool_code, params))
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return "ERROR: timeout (15s)."
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"


async def call_mcp_tool(tool_name: str, params: dict) -> str:
    if tool_name not in MCP_REGISTRY:
        return f"ERROR: MCP Tool '{tool_name}' not found."
    e = MCP_REGISTRY[tool_name]
    if e["func"]:
        if asyncio.iscoroutinefunction(e["func"]):
            return await e["func"](params)
        return e["func"](params)
    method, url = "GET", ""
    try:
        headers = {"Content-Type": "application/json"}
        if e.get("api_key"):
            headers["Authorization"] = f"Bearer {e['api_key']}"
        base_url = e["url"].rstrip("/")
        req_params = dict(params)
        path = req_params.pop("endpoint", req_params.pop("path", ""))
        method = req_params.pop("method", "").upper()
        if not method:
            if "notion" in tool_name.lower():
                method = "POST"
            elif any(kw in path.lower() for kw in ["search", "get", "list", "find"]):
                method = "GET"
            else:
                method = "GET"
        if path and not path.startswith("/"):
            path = "/" + path
        url = f"{base_url}{path}" if path else base_url
        if "notion" in base_url or "notion" in tool_name.lower():
            headers["Notion-Version"] = "2022-06-28"
        if "googleapis" in base_url:
            api_key = e.get("api_key", "")
            if api_key and not api_key.startswith("ya29."):
                headers.pop("Authorization", None)
                req_params["key"] = api_key
        if "api.telegram.org" in base_url:
            bot_token = e.get("api_key", "")
            if bot_token:
                headers.pop("Authorization", None)
                url = url.replace("/bot", f"/bot{bot_token}")
        if "discord.com" in base_url:
            bot_token = e.get("api_key", "")
            if bot_token and not bot_token.startswith("Bearer ") and not bot_token.startswith("Bot "):
                headers["Authorization"] = f"Bot {bot_token}"
        body_payload = req_params.pop("body", None) if method in ("POST", "PATCH", "PUT") else None
        payload = body_payload if body_payload is not None else (req_params if req_params else {})
        async with httpx.AsyncClient(timeout=15) as client:
            if method == "GET":
                resp = await client.get(url, params=req_params, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers, json=payload if payload else None)
            elif method == "PATCH":
                resp = await client.patch(url, json=payload, headers=headers)
            elif method == "PUT":
                resp = await client.put(url, json=payload, headers=headers)
            else:
                resp = await client.post(url, json=payload, headers=headers)
            body = resp.text[:4000]
            log_external_call(method, url, resp.status_code, body[:100])
            if resp.status_code >= 400:
                return f"HTTP ERROR {resp.status_code}: {body[:500]}"
            return body
    except Exception as ex:
        log_external_call(method, url, 0, str(ex)[:100])
        return f"MCP ERROR: {str(ex)}"

_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "max": max, "min": min, "range": range,
    "round": round, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
    "type": type, "zip": zip, "True": True, "False": False, "None": None,
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "json": json, "set": set, "reversed": reversed,
    "map": map, "filter": filter, "print": lambda *a, **kw: None,
}


def _run_tool_sync(tool_code, params):
    if not tool_code or not tool_code.strip():
        return "ERROR: empty tool code. Write a run(params) function."
    local_vars = {}
    try:
        exec(tool_code, {"__builtins__": _BUILTINS}, local_vars)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "run" in local_vars:
        try:
            r = local_vars["run"](params)
            return str(r) if r is not None else "(no output)"
        except Exception as e:
            return f"ERROR executing run(): {type(e).__name__}: {e}"
    return f"ERROR: function 'run(params)' not found in code. The tool must define 'def run(params):'."


def _fs_op(params: dict) -> str:
    op = params.get("op", "")
    path = params.get("path", "")
    if not path:
        return "ERROR: missing 'path' parameter."
    target = os.path.normpath(os.path.abspath(path))
    try:
        if op == "read":
            if not os.path.isfile(target):
                return f"ERROR: file not found: {path}"
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) > 50000:
                content = content[:50000] + "\n... (truncated)"
            return content
        elif op == "write":
            content = params.get("content", "")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return f"OK: wrote {len(content)} bytes to {path}"
        elif op == "append":
            content = params.get("content", "")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)
            return f"OK: appended {len(content)} bytes to {path}"
        elif op == "mkdir":
            os.makedirs(target, exist_ok=True)
            return f"OK: folder created/verified: {path}"
        elif op == "list":
            if not os.path.isdir(target):
                return f"ERROR: folder not found: {path}"
            entries = sorted(os.listdir(target))
            lines = []
            for e in entries:
                full = os.path.join(target, e)
                kind = "d" if os.path.isdir(full) else "f"
                size = os.path.getsize(full) if os.path.isfile(full) else 0
                lines.append(f"[{kind}] {e} ({size} bytes)")
            return "\n".join(lines) if lines else "(empty folder)"
        elif op == "delete":
            if os.path.isfile(target):
                os.remove(target)
                return f"OK: file deleted: {path}"
            elif os.path.isdir(target):
                shutil.rmtree(target)
                return f"OK: folder deleted: {path}"
            return f"ERROR: not found: {path}"
        elif op == "exists":
            return f"{'YES' if os.path.exists(target) else 'NO'}: {path}"
        elif op == "copy":
            dest = params.get("dest", "")
            if not dest:
                return "ERROR: missing 'dest' parameter."
            dest_path = os.path.normpath(os.path.abspath(dest))
            if os.path.isdir(target):
                shutil.copytree(target, dest_path)
            else:
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(target, dest_path)
            return f"OK: copied {path} -> {dest}"
        elif op == "move":
            dest = params.get("dest", "")
            if not dest:
                return "ERROR: missing 'dest' parameter."
            dest_path = os.path.normpath(os.path.abspath(dest))
            shutil.move(target, dest_path)
            return f"OK: moved {path} -> {dest}"
        elif op == "rename":
            new_name = params.get("new_name", "")
            if not new_name:
                return "ERROR: missing 'new_name' parameter."
            new_path = os.path.join(os.path.dirname(target), new_name)
            os.rename(target, new_path)
            return f"OK: renamed {path} -> {new_name}"
        elif op == "stat":
            if not os.path.exists(target):
                return f"ERROR: not found: {path}"
            st = os.stat(target)
            return f"type: {'dir' if os.path.isdir(target) else 'file'}, size: {st.st_size} bytes, modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}"
        else:
            return f"ERROR: operation '{op}' not supported. Options: read, write, append, mkdir, list, delete, exists, copy, move, rename, stat"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"


_current_project_for_tools = ""

async def _send_to_channel_tool(params: dict) -> str:
    channel_name = params.get("channel", "").lstrip("@")
    message = params.get("message", "")
    if not channel_name or not message:
        return "ERROR: 'channel' and 'message' parameters required."
    project_name = _current_project_for_tools
    if not project_name:
        return "ERROR: no active project."
    from config import get_project
    cfg = get_project(project_name)
    if not cfg:
        return f"ERROR: project {project_name} not found."
    ch = cfg.get("channels", {}).get(channel_name)
    if not ch:
        return f"ERROR: channel @{channel_name} not found."
    await _send_channel_reply(project_name, channel_name, ch, message)
    return f"OK: message sent to @{channel_name}."


def _register_filesystem_tool():
    register_mcp_tool(
        name="filesystem",
        url="",
        description="Read, write, create, delete files and folders on the PC. Options op: read, write, append, mkdir, list, delete, exists, copy, move, rename, stat. Parameters: op, path, content (for write/append), dest (for copy/move), new_name (for rename).",
        func=_fs_op,
        api_key=""
    )
    register_mcp_tool(
        name="send_to_channel",
        url="",
        description="Send a message to a messaging channel. Parameters: channel (name of the channel node, e.g. telegram), message (text to send).",
        func=_send_to_channel_tool,
        api_key=""
    )


_register_filesystem_tool()
