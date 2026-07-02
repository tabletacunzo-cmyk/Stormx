import json, os, asyncio, time, logging
from typing import Optional, Callable, Awaitable
import httpx

logger = logging.getLogger("stormx.channels")

TELEGRAM_API = "https://api.telegram.org/bot"
DISCORD_API = "https://discord.com/api/v10"
WHATSAPP_API = "https://graph.facebook.com/v21.0"

# Unified incoming-call log (Telegram/Discord/WhatsApp/webhook inbound).
INCOMING_LOG: list[dict] = []
MAX_INCOMING_ENTRIES = 300


def log_incoming(source: str, project: str, channel_id: str = "",
                 text: str = "", sender: str = "", payload: str = "",
                 status: int = 200, method: str = "IN"):
    """Record an inbound message/call from an external channel."""
    entry = {
        "ts": time.time(),
        "source": source,
        "project": project,
        "channel_id": channel_id,
        "from": sender,
        "text": str(text)[:300],
        "payload": str(payload)[:300],
        "status": status,
        "method": method,
        "direction": "in",
    }
    INCOMING_LOG.append(entry)
    while len(INCOMING_LOG) > MAX_INCOMING_ENTRIES:
        INCOMING_LOG.pop(0)
    return entry


class ChannelError(Exception):
    pass


class TelegramChannel:
    def __init__(self, bot_token: str, project: str = "", channel_node_id: str = ""):
        self.bot_token = bot_token
        self.project = project
        self.channel_node_id = channel_node_id
        self._base = f"{TELEGRAM_API}{bot_token}"
        self._offset = 0

    async def get_me(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self._base}/getMe")
            d = r.json()
            if not d.get("ok"):
                raise ChannelError(f"Telegram getMe failed: {d.get('description')}")
            return d["result"]

    async def set_webhook(self, webhook_url: str) -> bool:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self._base}/setWebhook", params={
                "url": webhook_url, "allowed_updates": json.dumps(["message"]),
            })
            ok = r.json().get("ok", False)
            if not ok:
                logger.warning("Telegram webhook setup failed")
            return ok

    async def delete_webhook(self) -> bool:
        async with httpx.AsyncClient(timeout=10) as c:
            return (await c.get(f"{self._base}/deleteWebhook")).json().get("ok", False)

    async def get_updates(self, timeout: int = 25) -> list:
        async with httpx.AsyncClient(timeout=timeout + 5) as c:
            r = await c.get(f"{self._base}/getUpdates", params={
                "offset": self._offset + 1, "timeout": timeout,
                "allowed_updates": json.dumps(["message"]),
            })
            d = r.json()
            if not d.get("ok"):
                desc = d.get("description", "unknown")
                if r.status_code == 409 or "Conflict" in desc or "webhook" in desc.lower():
                    raise ChannelError(f"409:{desc}")
                raise ChannelError(desc)
            results = d.get("result", [])
            for upd in results:
                uid = upd.get("update_id", 0)
                if uid >= self._offset:
                    self._offset = uid
            return results

    async def get_webhook_info(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self._base}/getWebhookInfo")
            d = r.json()
            return d.get("result", {}) if d.get("ok") else {}

    async def send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
        if not text or not chat_id:
            return False
        payload = {"chat_id": chat_id, "text": str(text)[:4000]}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{self._base}/sendMessage", json=payload)
                if r.status_code == 400 and parse_mode:
                    payload.pop("parse_mode", None)
                    r = await c.post(f"{self._base}/sendMessage", json=payload)
                return r.status_code == 200
        except Exception as e:
            logger.warning("Telegram sendMessage error: %s", e)
            return False

    async def listen_once(self) -> list[dict]:
        updates = await self.get_updates(timeout=5)
        messages = []
        for upd in updates:
            msg = upd.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", msg.get("caption", ""))
            if chat_id and text:
                messages.append({
                    "chat_id": chat_id, "text": text,
                    "from": msg.get("from", {}).get("first_name", "Unknown"),
                    "update_id": upd.get("update_id", 0),
                })
        return messages


class DiscordChannel:
    def __init__(self, bot_token: str, channel_id: str):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self._headers = {"Authorization": f"Bot {bot_token}"}

    async def get_messages(self, limit: int = 5) -> list[dict]:
        if not self.bot_token or not self.channel_id:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{DISCORD_API}/channels/{self.channel_id}/messages",
                                headers=self._headers, params={"limit": limit})
                if r.status_code >= 400:
                    return []
                return [
                    {"chat_id": self.channel_id, "text": m.get("content", ""),
                     "from": m.get("author", {}).get("username", "Unknown")}
                    for m in r.json() if m.get("content")
                ]
        except Exception:
            return []

    async def send_message(self, text: str) -> bool:
        if not self.bot_token or not self.channel_id or not text:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{DISCORD_API}/channels/{self.channel_id}/messages",
                                 headers=self._headers, json={"content": str(text)[:2000]})
                return r.status_code == 200
        except Exception:
            return False

    @staticmethod
    def verify_interaction_signature(bot_public_key: str, signature: str, timestamp: str, body: bytes) -> bool:
        """Verify Discord Ed25519 interaction signature. Returns True if unverifiable config (skip)."""
        if not bot_public_key or not signature or not timestamp:
            return True
        try:
            from nacl.signing import VerifyKey
            from nacl.exceptions import BadSignatureError
            vk = VerifyKey(bytes.fromhex(bot_public_key))
            try:
                vk.verify(timestamp.encode() + body, bytes.fromhex(signature))
                return True
            except BadSignatureError:
                return False
        except Exception:
            return True


class WhatsAppChannel:
    def __init__(self, api_token: str, phone_number_id: str):
        self.api_token = api_token
        self.phone_number_id = phone_number_id
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def send_message(self, to: str, text: str) -> bool:
        if not self.api_token or not self.phone_number_id or not text:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{WHATSAPP_API}/{self.phone_number_id}/messages",
                                 headers=self._headers, json={
                    "messaging_product": "whatsapp", "to": to,
                    "text": {"body": str(text)[:2000]},
                })
                return r.status_code == 200
        except Exception:
            return False

    @staticmethod
    def parse_inbound(payload: dict) -> list[dict]:
        """Extract inbound text messages from a WhatsApp Cloud API webhook payload."""
        out = []
        for entry in (payload or {}).get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                msgs = (change.get("value", {}) or {}).get("messages", []) or []
                for m in msgs:
                    if m.get("type") == "text":
                        text = (m.get("text", {}) or {}).get("body", "")
                    else:
                        text = ""
                    sender = (m.get("from", "") or "")
                    msg_id = m.get("id", "")
                    name = ""
                    contacts = (change.get("value", {}) or {}).get("contacts", []) or []
                    if contacts:
                        name = contacts[0].get("wa_id", "") or contacts[0].get("profile", {}).get("name", "")
                    if text or sender:
                        out.append({"from": sender, "name": name, "text": text, "message_id": msg_id})
        return out


class ChannelRegistry:
    def __init__(self):
        self._telegram: dict[str, TelegramChannel] = {}
        self._discord: dict[str, DiscordChannel] = {}
        self._whatsapp: dict[str, WhatsAppChannel] = {}
        self._by_project: dict[str, dict[str, object]] = {}
        self._callbacks: list[Callable] = []

    def register_telegram(self, bot_token: str, project: str = "",
                          channel_node_id: str = "") -> TelegramChannel:
        key = f"tg:{bot_token}"
        if key in self._telegram:
            return self._telegram[key]
        ch = TelegramChannel(bot_token, project, channel_node_id)
        self._telegram[key] = ch
        self._by_project.setdefault(project, {})[channel_node_id] = ch
        return ch

    def register_discord(self, bot_token: str, channel_id: str,
                         project: str = "", channel_node_id: str = "") -> DiscordChannel:
        key = f"dc:{bot_token}:{channel_id}"
        if key in self._discord:
            return self._discord[key]
        ch = DiscordChannel(bot_token, channel_id)
        self._discord[key] = ch
        self._by_project.setdefault(project, {})[channel_node_id] = ch
        return ch

    def register_whatsapp(self, api_token: str, phone_number_id: str,
                          project: str = "", channel_node_id: str = "") -> WhatsAppChannel:
        key = f"wa:{phone_number_id}"
        if key in self._whatsapp:
            return self._whatsapp[key]
        ch = WhatsAppChannel(api_token, phone_number_id)
        self._whatsapp[key] = ch
        self._by_project.setdefault(project, {})[channel_node_id] = ch
        return ch

    def get_telegram(self, bot_token: str) -> Optional[TelegramChannel]:
        return self._telegram.get(f"tg:{bot_token}")

    def get_by_project(self, project: str, node_id: str):
        return self._by_project.get(project, {}).get(node_id)

    def resolve_bot_token(self, project: str, channel_config: dict) -> str:
        token = channel_config.get("bot_token", "")
        if not token:
            env_key = f"TELEGRAM_BOT_TOKEN_{project.upper()}"
            token = os.environ.get(env_key, "")
        if not token:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        return token

    def on_message(self, cb: Callable):
        self._callbacks.append(cb)

    async def dispatch_message(self, project: str, channel_node_id: str,
                               chat_id: str, text: str):
        for cb in self._callbacks:
            try:
                await cb(project, channel_node_id, chat_id, text)
            except Exception as e:
                logger.error("Channel callback error: %s", e)

    def unregister_project(self, project: str):
        self._by_project.pop(project, None)


registry = ChannelRegistry()
