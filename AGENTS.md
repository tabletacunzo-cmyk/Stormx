# Stormx — appunti per l'assistente

## Comandi di verifica (da eseguire dopo ogni modifica all'app)

```bash
# Sintassi Python
python -c "import ast; [ast.parse(open(f,encoding='utf-8').read()) for f in ['app.py','channels.py','config.py','rag.py','settings.py','agents/__init__.py','selfcheck.py']]"

# Self-check funzionale end-to-end: avvia l'app, simula chiamate entranti
# da Telegram/Discord/WhatsApp/webhook e verifica che finiscano nel log.
python selfcheck.py
# Contro un'istanza già attiva:
python selfcheck.py --base-url http://localhost:7777
```

Lo script `selfcheck.py` è il meccanismo di auto-revisione: stampa un report
PASS/FAIL per ogni endpoint/chiamata entrante. Deve restituire 15/15 prima di
considerare concluso un lavoro sulle chiamate esterne o sui webhook.

## Mappa delle chiamate esterne (pagina "Chiamate esterne" / #/log)

Tutti gli inbound sono registrati in `channels.INCOMING_LOG` tramite
`log_incoming(source, project, channel_id, ...)` e aggregati in `GET /api/log`
insieme a `WEBHOOK_LOG` (webhook agent) e `agents.external_call_log` (outbound MCP).

Endpoint inbound:
- `POST /api/telegram/webhook/{project}/{channel_id}` → source=telegram
- `POST /api/discord/webhook/{project}/{channel_id}` → source=discord (Interactions, ACK type 1)
- `GET  /api/whatsapp/webhook/{project}/{channel_id}` → handshake verify (hub.challenge)
- `POST /api/whatsapp/webhook/{project}/{channel_id}` → source=whatsapp
- `POST /api/webhook/channel/{project}/{channel_id}` → source=tipo canale (webhook generico)
- `POST /api/webhook/{name}/{agent}` → source=webhook (trigger agente)

Il polling Telegram (long-poll) registra anch'esso i messaggi entranti.

## Convenzioni
- Nessun commento nel codice a meno che non sia richiesto.
- Dark theme, vanilla JS, file JSON su filesystem (niente DB).
- Le modifiche al frontend vanno in `static/script.js` / `static/index.html` / `static/style.css`.
