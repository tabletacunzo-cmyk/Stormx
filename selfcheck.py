"""Stormx self-check.

Avvia l'app in un subprocess, simola chiamate entranti da Telegram, Discord,
WhatsApp e webhook generici, e verifica che ognuna finisca nel log delle
"chiamate esterne" (/api/log). Stampa un report PASS/FAIL per ogni controllo.

Uso:
    python selfcheck.py
    python selfcheck.py --base-url http://localhost:7777   # contro istanza gia' attiva
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 7789


def log(msg: str):
    print(msg, flush=True)


def check(name: str, cond: bool, detail: str = ""):
    status = "\033[32mPASS\033[0m" if cond else "\033[31mFAIL\033[0m"
    log(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def ensure_project(base: str, name: str) -> dict:
    """Create a project if missing and return its config."""
    r = httpx.get(f"{base}/api/projects", timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"list projects failed: {r.status_code}")
    names = [p["name"] for p in r.json()]
    if name not in names:
        r = httpx.post(f"{base}/api/projects", json={"name": name}, timeout=10)
        r.raise_for_status()
    r = httpx.get(f"{base}/api/projects/{name}/config", timeout=10)
    return r.json()


def ensure_channel(base: str, project: str, ch_id: str, ch_cfg: dict) -> dict:
    r = httpx.post(
        f"{base}/api/projects/{project}/channels/{ch_id}",
        json=ch_cfg, timeout=10,
    )
    r.raise_for_status()
    return ch_cfg


def get_log(base: str) -> list:
    r = httpx.get(f"{base}/api/log", timeout=10)
    r.raise_for_status()
    return r.json().get("log", [])


def count_sources(log_entries: list, source: str, since: float) -> int:
    return sum(1 for e in log_entries if e.get("source") == source and e.get("ts", 0) >= since)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    base = args.base_url or f"http://127.0.0.1:{args.port}"
    proc = None

    if not args.base_url:
        env = dict(os.environ)
        env["PORT"] = str(args.port)
        env.setdefault("PUBLIC_URL", base)
        log(f"Avvio Stormx su {base} ...")
        proc = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=str(HERE),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for health
        ready = False
        for _ in range(40):
            try:
                r = httpx.get(f"{base}/api/health", timeout=2)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ready:
            log("\033[31mFAIL\033[0m — il server non risponde")
            proc.terminate()
            return 1
    log("Server attivo.\n")

    results = []
    project = f"selfcheck-{uuid.uuid4().hex[:6]}"
    ch_id = "ch_selfcheck"
    try:
        # Setup project + channels (one per source)
        ensure_project(base, project)
        ensure_channel(base, project, ch_id, {
            "type": "telegram", "enabled": True,
            "config": {"bot_token": "", "channel_id": "selfcheck-chat"},
        })
        ensure_channel(base, project, f"{ch_id}_dc", {
            "type": "discord", "enabled": True,
            "config": {"bot_token": "", "channel_id": "selfcheck-dc"},
        })
        ensure_channel(base, project, f"{ch_id}_wa", {
            "type": "whatsapp", "enabled": True,
            "config": {"api_token": "", "phone_number_id": "", "verify_token": "verify123"},
        })
        ensure_channel(base, project, f"{ch_id}_gw", {
            "type": "webhook", "enabled": True,
            "config": {"channel_id": "selfcheck-gw"},
        })

        log("== Controlli base ==")
        r = httpx.get(f"{base}/api/health", timeout=10)
        results.append(check("GET /api/health", r.status_code == 200, f"status={r.status_code}"))
        r = httpx.get(f"{base}/api/projects", timeout=10)
        results.append(check("GET /api/projects", r.status_code == 200 and any(p["name"] == project for p in r.json())))
        r = httpx.get(f"{base}/api/log", timeout=10)
        results.append(check("GET /api/log", r.status_code == 200 and isinstance(r.json().get("log"), list)))

        # Clear log to start clean
        httpx.post(f"{base}/api/log/clear", json={}, timeout=10)
        t0 = time.time()
        time.sleep(0.5)

        log("\n== Simulazione chiamate entranti ==")

        # 1) Telegram webhook inbound
        tg_payload = {
            "message": {
                "chat": {"id": 111111}, "text": "ciao da telegram selfcheck",
                "from": {"first_name": "SelfcheckTG"},
            },
            "update_id": 999999,
        }
        r = httpx.post(f"{base}/api/telegram/webhook/{project}/{ch_id}", json=tg_payload, timeout=15)
        results.append(check("Telegram webhook inbound", r.status_code == 200 and r.json().get("ok") is True))

        # 2) Discord interaction (ping + message)
        r = httpx.post(f"{base}/api/discord/webhook/{project}/{ch_id}_dc",
                       json={"type": 1}, timeout=15)
        results.append(check("Discord ping ACK", r.status_code == 200 and r.json().get("type") == 1))
        r = httpx.post(f"{base}/api/discord/webhook/{project}/{ch_id}_dc",
                       json={"type": 2, "data": {"content": "ciao da discord"},
                             "member": {"user": {"username": "SelfcheckDC"}}}, timeout=15)
        results.append(check("Discord message inbound", r.status_code == 200 and r.json().get("type") == 4))

        # 3) WhatsApp verify handshake
        r = httpx.get(f"{base}/api/whatsapp/webhook/{project}/{ch_id}_wa",
                      params={"hub.mode": "subscribe", "hub.challenge": "challenge123",
                              "hub.verify_token": "verify123"}, timeout=15)
        results.append(check("WhatsApp verify handshake", r.status_code == 200 and r.text == "challenge123"))

        # 4) WhatsApp inbound message
        wa_payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{"id": "m1", "type": "text", "text": {"body": "ciao da whatsapp"},
                                      "from": "393333333333"}],
                        "contacts": [{"wa_id": "393333333333", "profile": {"name": "SelfcheckWA"}}],
                    }
                }]
            }]
        }
        r = httpx.post(f"{base}/api/whatsapp/webhook/{project}/{ch_id}_wa", json=wa_payload, timeout=15)
        results.append(check("WhatsApp message inbound", r.status_code == 200 and r.json().get("status") == "ok"))

        # 5) Generic webhook channel (dedicated webhook-type channel -> source=webhook)
        r = httpx.post(f"{base}/api/webhook/channel/{project}/{ch_id}_gw",
                       json={"text": "ciao da webhook generico"}, timeout=15)
        results.append(check("Generic webhook inbound", r.status_code == 200 and r.json().get("status") == "ok"))

        # 6) Agent webhook (target a real agent if present, else orchestrator; may 404/500 without API key,
        #    but the inbound call must still be logged)
        cfg = ensure_project(base, project)
        agents = list(cfg.get("agents", {}).keys())
        agent_target = agents[0] if agents else "selfcheck_agent"
        if not agents:
            cfg.setdefault("agents", {})[agent_target] = {
                "enabled": True, "api_key": "", "api_url": "", "model": "",
                "temperature": 0.3, "prompt": "echo", "color": "#888", "tools": [],
            }
            httpx.post(f"{base}/api/projects/{project}/config", json=cfg, timeout=10)
        r = httpx.post(f"{base}/api/webhook/{project}/{agent_target}",
                       json={"trigger": "external", "data": "test"}, timeout=30)
        logged = any(e.get("source") == "webhook" and e.get("channel_id") == agent_target
                     and e.get("ts", 0) >= t0 for e in get_log(base))
        results.append(check("Agent webhook inbound logged", r.status_code in (200, 500) and logged,
                             f"status={r.status_code}"))

        # Let async tasks log
        time.sleep(1.5)
        log_entries = get_log(base)

        log("\n== Verifica registrazione nel log chiamate esterne ==")
        results.append(check("Telegram registrato nel log",
                             count_sources(log_entries, "telegram", t0) >= 1,
                             f"{count_sources(log_entries, 'telegram', t0)} entry"))
        results.append(check("Discord registrato nel log",
                             count_sources(log_entries, "discord", t0) >= 1,
                             f"{count_sources(log_entries, 'discord', t0)} entry"))
        results.append(check("WhatsApp registrato nel log",
                             count_sources(log_entries, "whatsapp", t0) >= 1,
                             f"{count_sources(log_entries, 'whatsapp', t0)} entry"))
        results.append(check("Webhook generico registrato nel log",
                             count_sources(log_entries, "webhook", t0) >= 1,
                             f"{count_sources(log_entries, 'webhook', t0)} entry"))

        # Field presence
        has_fields = all(
            all(e.get(k) is not None for k in ("source", "direction", "status", "ts"))
            for e in log_entries
        )
        results.append(check("Entry hanno campi source/direction/status/ts", has_fields))

    finally:
        log("\n== Pulizia ==")
        try:
            httpx.delete(f"{base}/api/projects/{project}", timeout=10)
            httpx.delete(f"{base}/api/projects/{project}_wh", timeout=10)
        except Exception:
            pass
        if proc:
            log("Terminazione server.")
            if os.name == "nt":
                proc.send_signal(signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    passed = sum(results)
    total = len(results)
    log(f"\n{'='*40}\nRisultato: {passed}/{total} controlli superati.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
