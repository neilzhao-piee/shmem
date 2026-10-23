#!/usr/bin/env python3
"""Aggregate recent conversation history from the connected agents into the shared journal.

Incremental: only sessions that changed since the last run are processed. For each
session it builds a compact transcript (user requests + assistant text; tool results
skipped), asks the configured model for durable facts, and appends the result to
journal/<YYYY-MM>.md under flock. If the model is unreachable (or not configured) it
falls back to deterministic keyword extraction, so the timer always produces something.

Session sources are pluggable adapters; two are included out of the box:
  - DSH agent:   JSONL (zstd) session files (DSH_SESSIONS_GLOB)
  - hermes:      SQLite state database (HERMES_STATE_DB)

To add another agent, write a generator that yields
    (session_key, change_marker, title, transcript, "YYYY-MM-DD")
and register it in SOURCES below. change_marker should be a value that changes
whenever the session grows (mtime:size, highest message id, ...).

Scheduled by a systemd user timer (see units/shmem-summarize.timer) or any cron.
"""
import glob
import json
import os
import re
import sqlite3
import subprocess
import urllib.request
from datetime import datetime, timezone
from fcntl import LOCK_EX, LOCK_UN, flock

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("SHMEM_TZ", "UTC"))
except Exception:
    TZ = timezone.utc

ROOT = os.environ.get("SHMEM_ROOT", ".")
STATE = os.path.join(ROOT, ".summarize-state.json")
LOG = os.path.join(ROOT, ".summarize.log")
LOCK = os.path.join(ROOT, ".lock")

ENDPOINT = os.environ.get("SUMMARIZE_MODEL_URL", "")
MODEL = os.environ.get("SUMMARIZE_MODEL", "")
MODEL_TIMEOUT = int(os.environ.get("SUMMARIZE_MODEL_TIMEOUT", "120"))
MAX_TOKENS = int(os.environ.get("SUMMARIZE_MAX_TOKENS", "4000"))

MAX_SESSIONS_PER_RUN = 12
MAX_TRANSCRIPT_CHARS = 3500
MAX_OUTPUT_BYTES = 4000
MIN_CONTENT_CHARS = 300

DECISION_RE = re.compile(
    r"(决定|改用|改成|确认|已部署|部署到|修复|根因|版本|commit|路径|端口|约定|规则|不能|必须|限制|发现|验证"
    r"|decided|changed|confirmed|deployed|fixed|root cause|version|commit|path|port|agreed|rule|must|limit|found)"
)


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), msg))
    except Exception:
        pass


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception as exc:
        log("state write failed: %s" % exc)


NOISE_PREFIX = (
    "<system-reminder>", "current runtime context", "time sampled", "instructions from:",
    "this is an automatically generated checkpoint", "you are an ai agent powered by",
    "the following workspace instructions",
)


def is_noise(text):
    low = text[:120].lower()
    return any(low.startswith(p) for p in NOISE_PREFIX)


def blocks_text(content):
    """Flatten message content (list of blocks or plain string) to text only."""
    if isinstance(content, str):
        return content.strip()
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = (b.get("text") or "").strip()
                if t:
                    out.append(t)
    return "\n".join(out)


def read_jsonl_zstd(path):
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(open(path, "rb").read())
    except ImportError:
        r = subprocess.run(["zstd", "-d", "-c", path], capture_output=True, timeout=20)
        return r.stdout if r.returncode == 0 else b""
    except Exception as exc:
        log("read %s: %s" % (path, exc))
        return b""


def dsh_sessions():
    """Adapter: DSH agent session files (JSONL, zstd-compressed)."""
    pattern = os.path.expanduser(os.environ.get(
        "DSH_SESSIONS_GLOB", "~/.dsh/sessions/*/*/session.v4.jsonl.zstd"))
    for path in sorted(glob.glob(pattern)):
        raw = read_jsonl_zstd(path)
        if not raw:
            continue
        try:
            st = os.stat(path)
            marker = "%d:%d" % (int(st.st_mtime), st.st_size)
        except Exception:
            continue
        lines, first_user = [], ""
        last_time = 0
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = rec.get("type")
            data = rec.get("data") or {}
            if rec.get("time"):
                last_time = max(last_time, int(rec["time"]))
            if t == "user/message":
                txt = blocks_text(data.get("content"))
                if txt and not is_noise(txt):
                    lines.append("user: %s" % txt[:400])
                    if not first_user:
                        first_user = txt[:200]
            elif t == "assistant/message":
                msg = data.get("message") or {}
                txt = blocks_text(msg.get("content"))
                if txt:
                    lines.append("assistant: %s" % txt[:300])
        if lines:
            date = datetime.fromtimestamp(last_time / 1000.0, TZ).strftime("%Y-%m-%d")
            yield os.path.basename(os.path.dirname(path)), marker, first_user, "\n".join(lines), date


def hermes_sessions():
    """Adapter: hermes SQLite state database (sessions + messages tables)."""
    db = os.path.expanduser(os.environ.get("HERMES_STATE_DB", "~/.hermes/state.db"))
    if not os.path.exists(db):
        return
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    except Exception as exc:
        log("hermes db: %s" % exc)
        return
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id, title, last_activity_at from sessions "
        "where archived=0 and hidden=0 order by last_activity_at desc limit 40"
    ).fetchall()
    for r in rows:
        msgs = con.execute(
            "select role, content, id from messages where session_id=? and active=1 "
            "and role in ('user','assistant') order by id",
            (r["id"],),
        ).fetchall()
        lines, first_user, max_id = [], "", 0
        for m in msgs:
            max_id = max(max_id, m["id"])
            txt = (m["content"] or "").strip()
            if not txt:
                continue
            if m["role"] == "user":
                if is_noise(txt):
                    continue
                lines.append("user: %s" % txt[:400])
                if not first_user:
                    first_user = txt[:200]
            else:
                lines.append("assistant: %s" % txt[:300])
        if lines:
            date = datetime.fromtimestamp(r["last_activity_at"], TZ).strftime("%Y-%m-%d")
            yield r["id"], str(max_id), r["title"] or first_user[:120], "\n".join(lines), date
    con.close()


SOURCES = (("dsh", dsh_sessions), ("hermes", hermes_sessions))


def ask_model(transcript, date):
    prompt = (
        "You are a memory archivist. Extract ONLY durable facts from the transcript below "
        "— things that remain true later: decisions, configuration, paths, ports, versions, "
        "root causes, agreements. Hard requirements: each fact starts with '- ', one line per "
        "fact, at most 6 facts; the only date you may use is the session date "
        + date + " (never invent other dates); write only what the transcript explicitly states, "
        "no speculation; if there is nothing durable output exactly NONE; "
        "never write passwords or keys — locations only.\n\nSession date: " + date + "\nTranscript:\n" + transcript[:MAX_TRANSCRIPT_CHARS]
    )
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT) as resp:
        d = json.loads(resp.read().decode("utf-8", "replace"))
    c = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return c.strip()


YEAR_RE = re.compile(r"\b(20\d{2})\b")
# Never let a credential value into shared memory; locations are fine.
SECRET_RE = re.compile(
    r"(?:password|passwd|密码|密钥|token|secret|api[ _-]?key)\s*(?:[:=＝]\s*|[A-Za-z0-9]{6,})",
    re.I,
)


def clean_facts(text, date):
    """Keep only bullet lines that do not invent a date outside the session year."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        if SECRET_RE.search(line):
            continue
        m = YEAR_RE.search(line)
        if m and m.group(1) != date[:4]:
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return "\n".join(out[:6])


def heuristic(transcript):
    return "\n".join([l for l in transcript.splitlines() if DECISION_RE.search(l)][:6])


def main():
    state = load_state()
    month = datetime.now(TZ).strftime("%Y-%m")
    os.makedirs(os.path.join(ROOT, "journal"), exist_ok=True)
    journal = os.path.join(ROOT, "journal", "%s.md" % month)
    processed = written = 0
    if not ENDPOINT or not MODEL:
        log("no SUMMARIZE_MODEL_URL/SUMMARIZE_MODEL configured — using keyword fallback only")

    for agent, gen in ((name, fn) for name, fn in SOURCES):
        for key, marker, title, transcript, date in gen():
            if processed >= MAX_SESSIONS_PER_RUN:
                break
            sid = "%s:%s" % (agent, key)
            if state.get(sid) == marker:
                continue
            if len(transcript) < MIN_CONTENT_CHARS:
                state[sid] = marker
                continue
            if ENDPOINT and MODEL:
                try:
                    raw_facts, how = ask_model(transcript, date), "model"
                except Exception as exc:
                    raw_facts, how = heuristic(transcript), "fallback(%s)" % type(exc).__name__
            else:
                raw_facts, how = "", "no-model"
            facts = clean_facts(raw_facts, date)
            if not facts:
                facts = clean_facts(heuristic(transcript), date) or (heuristic(transcript) if how != "model" else "")
            state[sid] = marker
            processed += 1
            if not facts or facts.strip().upper() == "NONE":
                continue
            if written + len(facts) > MAX_OUTPUT_BYTES:
                break
            safe_title = SECRET_RE.sub("[redacted]", title or "")[:120]
            block = "\n### auto-summary · %s · %s · %s\n%s\n" % (date, agent, safe_title, facts)
            with open(LOCK, "w") as lf:
                flock(lf, LOCK_EX)
                try:
                    with open(journal, "a", encoding="utf-8") as fh:
                        fh.write(block)
                finally:
                    flock(lf, LOCK_UN)
            written += len(facts)
            log("%s %s %s (%d chars)" % (agent, key, how, len(facts)))

    save_state(state)
    if written:
        try:
            subprocess.run(["bash", os.path.join(ROOT, "tools", "make-digest.sh")], timeout=20)
        except Exception:
            pass
    log("run done: processed=%d written=%d" % (processed, written))
    print("processed=%d written=%d" % (processed, written))


if __name__ == "__main__":
    main()
