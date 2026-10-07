"""Telegram approval bot (separate bot from the ordering backend's).

- Long polling (getUpdates), not a webhook: nothing new is exposed to the internet.
- Only the owner's chat id is obeyed. Everything else is ignored and logged (never answered, so the bot does not
  confirm it exists to strangers).
- Button data is short and carries only ids; every action re-checks state in the registry.
"""

from __future__ import annotations

import html
import logging

import httpx

from intel.serve.registry import REASON_CODES, Registry, RegistryError

log = logging.getLogger("intel.telegram")
H = html.escape


class Telegram:
    def __init__(self, token: str, chat_id: int, client: httpx.Client | None = None):
        self.chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{token}/"
        self._http = client or httpx.Client(timeout=40)

    def call(self, method: str, **params) -> dict | list | bool:
        r = self._http.post(self._base + method, json=params)
        data = r.json()
        if not data.get("ok"):
            # never log the URL: it contains the token
            raise RuntimeError(f"telegram {method} failed: {data.get('description')}")
        return data["result"]

    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int:
        params = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {
                "inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]
            }
        return self.call("sendMessage", **params)["message_id"]

    def edit(self, message_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        params = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in (buttons or [])]
            },
        }
        try:
            self.call("editMessageText", **params)
        except RuntimeError as e:  # "message is not modified" etc. must not break the loop
            log.warning("edit failed: %s", e)

    def answer(self, callback_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])
        except RuntimeError as e:
            log.warning("answerCallbackQuery failed: %s", e)

    def updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"])


# ---------- message texts ----------


def _pct(x) -> str:
    return "–" if x is None else f"{x:.3f}"


def approval_text(meta: dict, approval_id: int, public_url: str | None) -> str:
    s, c, j = meta["scores"]["challenger"], meta["scores"]["champion"], meta["judge"]
    head = (
        "🧪 <b>Baseline</b>: same recipe as today's predictor, retrained. No improvement claimed; approving it "
        "lets the new system start serving."
        if meta["purpose"] == "baseline"
        else "🚀 <b>New model passed all automatic checks</b>"
    )
    gates = "\n".join(
        f"{'✅' if g['status'] == 'pass' else '⏳' if g['status'] == 'pending' else '❌'} {H(g['gate'])}"
        for g in j["gates"]
    )
    link = f'\n\n<a href="{H(public_url)}/approvals/{approval_id}">Open details</a>' if public_url else ""
    return (
        f"{head}\n\n<b>{H(meta['name'])}</b> <code>{H(meta['version'])}</code>\n"
        f"Trained on pull <code>{H(meta['data']['pull_id'])}</code>\n\n"
        f"<pre>              new    now\n"
        f"balanced acc  {_pct(s['balanced_accuracy'])}  {_pct(c['balanced_accuracy'])}\n"
        f"AUC           {_pct(s['roc_auc'])}  {_pct(c['roc_auc'])}\n"
        f"Brier         {_pct(s['brier'])}  {_pct(c['brier'])}\n"
        f"test posts    {s['n']:>5}  {c['n']:>5}</pre>\n"
        f"{gates}{link}"
    )


def approval_buttons(approval_id: int) -> list[list[tuple[str, str]]]:
    return [[("✅ Approve", f"ap:{approval_id}"), ("❌ Reject", f"rj:{approval_id}")]]


HELP = (
    "<b>DK Intelligence</b>\n"
    "/status: models in use and waiting approvals\n"
    "/rollback: switch a model back to the previous version\n"
    "Approve or reject new models with the buttons on each message."
)


# ---------- update handling ----------


class Bot:
    def __init__(self, tg: Telegram, registry: Registry, public_url: str | None, on_change=lambda: None):
        self.tg, self.reg, self.public_url, self.on_change = tg, registry, public_url, on_change

    def authorised(self, update: dict) -> bool:
        if "callback_query" in update:
            q = update["callback_query"]
            return q.get("from", {}).get("id") == self.tg.chat_id
        m = update.get("message") or {}
        return m.get("chat", {}).get("id") == self.tg.chat_id and m.get("from", {}).get("id") == self.tg.chat_id

    def handle(self, update: dict) -> None:
        if not self.authorised(update):
            src = (update.get("message") or update.get("callback_query") or {}).get("from", {})
            log.warning("ignored update from unauthorised user id=%s", src.get("id"))
            from intel.serve.db import log_event

            log_event(self.reg.dsn, "telegram_unauthorised", user_id=src.get("id"), username=src.get("username"))
            return
        if "callback_query" in update:
            self._callback(update["callback_query"])
        else:
            self._command((update["message"].get("text") or "").strip())

    def _command(self, text: str) -> None:
        cmd = text.split()[0].split("@")[0].lower() if text else ""
        if cmd == "/status":
            self.tg.send(self.status_text())
        elif cmd == "/rollback":
            names = sorted(self.reg.active())
            if not names:
                self.tg.send("No model is active yet, so there is nothing to roll back.")
            else:
                self.tg.send(
                    "Roll back which model to its previous version?",
                    [[(f"↩️ {n}", f"rb:{n}")] for n in names] + [[("Cancel", "x")]],
                )
        else:
            self.tg.send(HELP)

    def status_text(self) -> str:
        act, pend = self.reg.active(), self.reg.pending()
        lines = ["<b>Models in use</b>"]
        lines += [
            f"• {H(n)}: <code>{H(r['version'])}</code> since {r['activated_at']:%d %b %H:%M} UTC ({H(r['reason'])})"
            for n, r in sorted(act.items())
        ] or ["• none yet"]
        lines.append("\n<b>Waiting for you</b>")
        lines += [
            f"• {H(p['name'])} <code>{H(p['version'])}</code> (expires {p['expires_at']:%d %b})" for p in pend
        ] or ["• nothing"]
        try:
            from intel.serve.measure import live_summary

            live = live_summary(self.reg.dsn, "7d")
            lines.append(f"\n<b>Live ledger</b>\n• {live['posts']} posts scored, {live['outcomes']} outcomes recorded")
            for n, m in sorted(live["models"].items()):
                ba = m["balanced_accuracy"] if m["balanced_accuracy"] is not None else "–"
                lines.append(f"• {H(n)}: {m['n']} posts at 7 days, accuracy {m['accuracy']:.2f}, balanced {ba}")
        except Exception:
            lines.append("\n(live ledger not available)")
        return "\n".join(lines)

    def _callback(self, q: dict) -> None:
        data, msg_id, user = q.get("data", ""), q.get("message", {}).get("message_id"), str(q["from"]["id"])
        kind, _, arg = data.partition(":")
        try:
            if kind == "ap":
                r = self.reg.approve(int(arg), user, "telegram")
                self.on_change()
                self.tg.answer(q["id"], "Approved")
                self.tg.edit(
                    msg_id,
                    f"✅ <b>Approved</b>: {H(r['name'])} <code>{H(r['version'])}</code> is now live.\n"
                    "Undo any time with /rollback.",
                )
            elif kind == "rj":
                self.tg.answer(q["id"])
                self.tg.edit(
                    msg_id,
                    "Why reject? (one tap, it helps the next models)",
                    [[(label, f"rr:{arg}:{code}")] for code, label in REASON_CODES.items()]
                    + [[("← Back", f"bk:{arg}")]],
                )
            elif kind == "rr":
                aid, code = arg.split(":", 1)
                r = self.reg.reject(int(aid), user, "telegram", code)
                self.tg.answer(q["id"], "Rejected")
                self.tg.edit(
                    msg_id,
                    f"❌ <b>Rejected</b>: {H(r['name'])} <code>{H(r['version'])}</code> "
                    f"({H(REASON_CODES[code])}). The current model keeps running.",
                )
            elif kind == "bk":
                a = self.reg.approval(int(arg))
                self.tg.answer(q["id"])
                if a:
                    self.tg.edit(
                        msg_id, approval_text(a["metadata"], a["id"], self.public_url), approval_buttons(a["id"])
                    )
            elif kind == "rb":
                r = self.reg.rollback(arg, user, "telegram")
                self.on_change()
                self.tg.answer(q["id"], "Rolled back")
                self.tg.edit(msg_id, f"↩️ <b>Rolled back</b>: {H(r['name'])} now runs <code>{H(r['version'])}</code>.")
            else:
                self.tg.answer(q["id"])
                self.tg.edit(msg_id, "Cancelled.")
        except (RegistryError, ValueError) as e:
            self.tg.answer(q["id"], str(e))
            self.tg.edit(msg_id, f"⚠️ {H(str(e))}")
