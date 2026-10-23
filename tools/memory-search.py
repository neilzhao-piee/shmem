#!/usr/bin/env python3
"""shmem retrieval layer: markdown stays the source of truth, this only adds
query-time recall.

- Indexes the shmem root markdown (INDEX.md, facts/, journal/, inbox/) into SQLite FTS5.
- CJK-friendly tokenizer: CJK runs become character bigrams (jieba-free), Latin words as-is.
  Plain FTS5 tokenizers split Chinese per character, which loses 2-3 char words.
- Optional vector layer fused with BM25 by reciprocal rank fusion (off by default).
- Secret guard: chunks containing known credential values or obvious secret patterns are not indexed.
- Two faces: CLI (for shell-capable agents) and MCP stdio server (for MCP-capable hosts).

Usage:
  memory-search.py "query" [-n 8] [--json] [--vector]
  memory-search.py --rebuild
  memory-search.py --stats
  memory-search.py --mcp            # stdio MCP server: tools/list + tools/call
  memory-search.py --mcp --allow-write   # also expose the three write tools

Env:
  SHMEM_ROOT            memory directory to index (default: current working directory)
  SHMEM_DB              index path (default: <root>/.index/memory.db)
  SHMEM_SECRET_FILES    colon-separated list of credential files whose values must never
                        be indexed, e.g. /home/me/.secrets:~/.api-key
  SHMEM_EMBED_BASE_URL  OpenAI-compatible embeddings base, e.g. http://127.0.0.1:11434/v1
  SHMEM_EMBED_MODEL     embedding model name
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import fcntl  # POSIX advisory locking for the journal append channel
except ImportError:
    fcntl = None  # Windows: write tools refuse to run rather than corrupt the channel

SM = Path(os.environ.get("SHMEM_ROOT", os.getcwd())).resolve()
DB = Path(os.environ.get("SHMEM_DB", str(SM / ".index" / "memory.db")))
EMBED_BASE = os.environ.get("SHMEM_EMBED_BASE_URL", "")
EMBED_MODEL = os.environ.get("SHMEM_EMBED_MODEL", "")
MAX_CHUNK_LINES = 30

SECRET_RE = re.compile(r"(?:password|passwd|口令|密码|secret|api[ _-]?key|token)\s*[:=]\s*\S{6,}", re.I)

LATIN = re.compile(r"[A-Za-z0-9_]{2,}")
CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ]+")


def tokenize(text):
    """Latin words + CJK character bigrams (single char if the run is one char)."""
    out = [m.group(0).lower() for m in LATIN.finditer(text)]
    for m in CJK.finditer(text):
        run = m.group(0)
        if len(run) == 1:
            out.append(run)
        else:
            for i in range(len(run) - 1):
                out.append(run[i:i + 2])
    return out


def cred_files():
    """Colon-separated list from SHMEM_SECRET_FILES, expanded. Missing files are skipped."""
    raw = os.environ.get("SHMEM_SECRET_FILES", "")
    return [os.path.expanduser(p.strip()) for p in raw.split(":") if p.strip()]


def cred_values():
    vals = set()
    for p in cred_files():
        try:
            raw = Path(p).read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        for line in raw.splitlines():
            v = line.split("=", 1)[-1].strip() if "=" in line else line.strip()
            if len(v) >= 6 and not v.startswith("#"):
                vals.add(v)
    return vals


def chunk_file(path, secrets):
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    heading = ""
    chunk, start = [], None
    for i, line in enumerate(lines, 1):
        if line.startswith("#"):
            heading = line.strip("# ").strip()
        if not line.strip():
            if chunk:
                yield path, start, i - 1, heading, "\n".join(chunk)
                chunk, start = [], None
            continue
        text = line.strip()
        if SECRET_RE.search(text) or any(v in text for v in secrets):
            continue
        if start is None:
            start = i
        chunk.append(text)
        if len(chunk) >= MAX_CHUNK_LINES:
            yield path, start, i, heading, "\n".join(chunk)
            chunk, start = [], None
    if chunk:
        yield path, start, len(lines), heading, "\n".join(chunk)


def open_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS docs(
        id INTEGER PRIMARY KEY, path TEXT, start_line INT, end_line INT,
        heading TEXT, text TEXT, mtime REAL, sha TEXT)""")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(tokens, heading, tokenize='unicode61')")
    con.execute("CREATE TABLE IF NOT EXISTS embed(doc_id INTEGER PRIMARY KEY, vec BLOB)")
    con.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, hash TEXT, mtime REAL)")
    return con


def index_file(con, path, secrets, vector=False):
    raw = path.read_bytes()
    h = hashlib.sha256(raw).hexdigest()
    mtime = path.stat().st_mtime
    con.execute("INSERT OR REPLACE INTO files(path,hash,mtime) VALUES(?,?,?)", (str(path), h, mtime))
    ids = [r[0] for r in con.execute("SELECT id FROM docs WHERE path=?", (str(path),)).fetchall()]
    if ids:
        con.executemany("DELETE FROM fts WHERE rowid=?", ((i,) for i in ids))
        con.executemany("DELETE FROM embed WHERE doc_id=?", ((i,) for i in ids))
    con.execute("DELETE FROM docs WHERE path=?", (str(path),))
    n = 0
    for p, s, e, heading, text in chunk_file(path, secrets):
        sha = f"{s}-{e}"
        toks = " ".join(tokenize(text))
        if not toks:
            continue
        cur = con.execute("INSERT INTO docs(path,start_line,end_line,heading,text,mtime,sha) VALUES(?,?,?,?,?,?,?)",
                          (str(p), s, e, heading, text, mtime, sha))
        con.execute("INSERT INTO fts(rowid, tokens, heading) VALUES(?,?,?)", (cur.lastrowid, toks, heading))
        n += 1
        if vector and EMBED_BASE and EMBED_MODEL:
            v = embed(text)
            if v:
                con.execute("INSERT OR REPLACE INTO embed(doc_id, vec) VALUES(?,?)",
                            (cur.lastrowid, struct.pack(f"{len(v)}f", *v)))
    return n


def md_files():
    return [p for p in SM.rglob("*.md") if ".index" not in p.parts]


def sync(con, vector=False):
    """Re-index only files whose content hash changed (cheap for typical memory sizes)."""
    changed = [p for p in md_files()
               if (row := con.execute("SELECT hash FROM files WHERE path=?", (str(p),)).fetchone()) is None
               or row[0] != hashlib.sha256(p.read_bytes()).hexdigest()]
    if not changed:
        return 0
    secrets = cred_values()
    n = 0
    for path in changed:
        n += index_file(con, path, secrets, vector)
    con.commit()
    return n


def rebuild(con, vector=False):
    secrets = cred_values()
    con.execute("DELETE FROM docs"); con.execute("DELETE FROM fts"); con.execute("DELETE FROM embed")
    n = 0
    for path in sorted(md_files()):
        n += index_file(con, path, secrets, vector)
    con.commit()
    return n


def embed(text):
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{EMBED_BASE.rstrip('/')}/embeddings",
            data=json.dumps({"model": EMBED_MODEL, "input": [text[:1500]]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["data"][0]["embedding"]
    except Exception as e:
        sys.stderr.write(f"embed failed: {e}\n")
        return None


def bm25(con, query, limit):
    toks = tokenize(query)
    if not toks:
        return []
    match = " OR ".join(f'"{t.replace(chr(34), chr(34)+chr(34))}"' for t in toks[:24])
    rows = con.execute(
        "SELECT rowid, bm25(fts, 1.0, 3.0) AS score FROM fts WHERE fts MATCH ? ORDER BY score LIMIT ?",
        (match, limit * 3)).fetchall()
    return [(rid, -sc) for rid, sc in rows]


def vector_recall(con, query, limit):
    if not (EMBED_BASE and EMBED_MODEL):
        return []
    v = embed(query)
    if not v:
        return []
    rows = con.execute("SELECT doc_id, vec FROM embed").fetchall()
    scored = []
    for doc_id, blob in rows:
        vec = struct.unpack(f"{len(blob) // 4}f", blob)
        if len(vec) != len(v):
            continue
        dot = sum(a * b for a, b in zip(v, vec))
        na = sum(a * a for a in v) ** .5
        nb = sum(b * b for b in vec) ** .5
        if na and nb:
            scored.append((doc_id, dot / (na * nb)))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit * 3]


def fuse(bm, vv, limit):
    ranks = {}
    for pos, (rid, _s) in enumerate(bm):
        ranks[rid] = ranks.get(rid, 0) + 1.0 / (60 + pos + 1)
    for pos, (rid, _s) in enumerate(vv):
        ranks[rid] = ranks.get(rid, 0) + 1.0 / (60 + pos + 1)
    return sorted(ranks.items(), key=lambda x: -x[1])[:limit]


def doc_row(con, rid):
    return con.execute("SELECT path,start_line,end_line,heading,text FROM docs WHERE id=?", (rid,)).fetchone()


def search(query, limit=8, vector=False, con=None):
    own = con is None
    if own:
        con = open_db()
    sync(con, vector)
    bm = bm25(con, query, limit)
    vv = vector_recall(con, query, limit) if vector else []
    ids = fuse(bm, vv, limit) if vv else [(rid, 1.0 / (i + 1)) for i, (rid, _s) in enumerate(bm)]
    out = []
    for rid, score in ids:
        path, s, e, heading, text = doc_row(con, rid)
        out.append({"path": path, "lines": [s, e], "heading": heading,
                    "score": round(score, 4), "text": text})
    if own:
        con.close()
    return out


def stats(con):
    n = con.execute("SELECT count(*) FROM docs").fetchone()[0]
    files = con.execute("SELECT count(DISTINCT path) FROM docs").fetchone()[0]
    emb = con.execute("SELECT count(*) FROM embed").fetchone()[0]
    return {"docs": n, "files": files, "embeddings": emb, "db": str(DB), "vector_enabled": bool(EMBED_BASE and EMBED_MODEL)}


# ---------- Write face (three channels, protocol-enforced; MCP-only, opt-in) ----------
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.environ.get("SHMEM_TZ", "UTC")))
    except Exception:
        return datetime.now()


def secret_hit(text):
    """Reason string if this text must never enter memory, else None."""
    m = SECRET_RE.search(text)
    if m:
        return f"secret pattern ({m.group(0)[:40]!r})"
    for v in cred_values():
        if v and v in text:
            return "credential value from SHMEM_SECRET_FILES"
    return None


def channel_write(con, kind, args):
    """Apply one write through its protocol channel. Returns (ok, message)."""
    if fcntl is None:
        return False, "write tools require POSIX flock; unavailable on this platform"
    if kind == "memory_write":
        topic, content = args.get("topic", ""), args.get("content", "")
        if not SLUG.match(topic):
            return False, f"topic must be a kebab slug (facts/<topic>.md), got {topic!r}"
        target = SM / "facts" / f"{topic}.md"
        mode, payload = "w", content
    elif kind == "memory_append":
        entry = args.get("entry", "")
        target = SM / "journal" / f"{now():%Y-%m}.md"
        mode, payload = "a", f"\n## {now():%Y-%m-%d}\n\n{entry}\n"
    elif kind == "inbox_write":
        agent, content = args.get("agent", ""), args.get("content", "")
        if not SLUG.match(agent):
            return False, f"agent must be a kebab slug (inbox/TO-<agent>.md), got {agent!r}"
        if content.count("\n") + 1 > 20:
            return False, "inbox message must be <= 20 lines"
        target = SM / "inbox" / f"TO-{agent}.md"
        mode, payload = "w", content
    else:
        return False, f"unknown write channel {kind!r}"
    hit = secret_hit(payload)
    if hit:
        return False, f"refused: {hit} — never write secrets, only their location"
    lock_path = SM / ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, mode, encoding="utf-8") as f:
                f.write(payload)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    sync(con)  # incremental re-index of the changed file only
    return True, f"wrote {target.relative_to(SM)}"


# ---------- MCP face (minimal stdio JSON-RPC, no deps) ----------
def mcp_loop(allow_write=False):
    con = open_db()
    tools = [{"name": "memory_search",
              "description": f"Query the shared memory (markdown at {SM}). Returns passages with file path and line numbers.",
              "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                              "required": ["query"]}},
             {"name": "memory_stats", "description": "Index size and whether the vector layer is active.",
              "inputSchema": {"type": "object", "properties": {}}}]
    if allow_write:
        tools += [
            {"name": "memory_write",
             "description": "Rewrite facts/<topic>.md in place (current truth only). Refuses content matching the secret guard.",
             "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}, "content": {"type": "string"}},
                             "required": ["topic", "content"]}},
            {"name": "memory_append",
             "description": "Append a dated section to journal/<YYYY-MM>.md under the protocol lock.",
             "inputSchema": {"type": "object", "properties": {"entry": {"type": "string"}}, "required": ["entry"]}},
            {"name": "inbox_write",
             "description": "Overwrite inbox/TO-<agent>.md (<= 20 lines: conclusion + pointers + ask).",
             "inputSchema": {"type": "object", "properties": {"agent": {"type": "string"}, "content": {"type": "string"}},
                             "required": ["agent", "content"]}}]

    def reply(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        rid = msg.get("id")
        if method == "initialize":
            reply({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "shmem", "version": "0.1.0"}}})
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue
        elif method == "tools/list":
            reply({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            name = msg.get("params", {}).get("name")
            args = msg.get("params", {}).get("arguments", {})
            if name == "memory_search":
                res = search(args.get("query", ""), int(args.get("limit", 8)), vector=False, con=con)
                content = json.dumps(res, ensure_ascii=False, indent=1)
            elif name == "memory_stats":
                sync(con)
                content = json.dumps(stats(con), ensure_ascii=False)
            elif name in ("memory_write", "memory_append", "inbox_write"):
                if not allow_write:
                    reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool {name}"}})
                    continue
                ok, msg = channel_write(con, name, args)
                reply({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": msg}],
                        "isError": not ok}})
                continue
            else:
                reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool {name}"}})
                continue
            reply({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": content}]}})
        else:
            reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})


def main():
    ap = argparse.ArgumentParser(description="shmem query-time recall (BM25 + optional vector)")
    ap.add_argument("query", nargs="?", help="query text (CJK ok)")
    ap.add_argument("-n", "--limit", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--vector", action="store_true", help="fuse BM25 with vector recall (needs SHMEM_EMBED_BASE_URL/SHMEM_EMBED_MODEL)")
    ap.add_argument("--rebuild", action="store_true", help="incremental: re-index files changed since last index")
    ap.add_argument("--full", action="store_true", help="drop the index and rebuild from scratch")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--mcp", action="store_true", help="run as stdio MCP server")
    ap.add_argument("--allow-write", action="store_true",
                    help="with --mcp: expose memory_write / memory_append / inbox_write (default face is read-only)")
    a = ap.parse_args()

    con = open_db()
    did = False
    if a.full:
        n = rebuild(con, vector=a.vector)
        print(f"rebuilt {n} chunks"); did = True
    elif a.rebuild:
        n = sync(con, vector=a.vector)
        print(f"re-indexed {n} chunks (changed files only)"); did = True
    if a.stats:
        print(json.dumps(stats(con), ensure_ascii=False, indent=1)); did = True
    if a.mcp:
        mcp_loop(allow_write=a.allow_write); return
    if not a.query:
        if not did:
            ap.print_help()
        return
    res = search(a.query, a.limit, a.vector, con=con)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif not res:
        print("(no match — do not invent memory)")
    else:
        for r in res:
            print(f"{r['path']}:{r['lines'][0]}-{r['lines'][1]}  [{r['heading']}]  {r['score']}")
            print(r["text"])
            print()


if __name__ == "__main__":
    main()
