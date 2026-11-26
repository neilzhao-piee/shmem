#!/usr/bin/env python3
"""shmem retrieval layer: markdown stays the source of truth, this only adds
query-time recall.

- Indexes the shmem root markdown (INDEX.md, facts/, journal/, inbox/) into SQLite FTS5.
- CJK-friendly tokenizer: CJK runs become character bigrams (jieba-free), Latin words as-is.
  Plain FTS5 tokenizers split Chinese per character, which loses 2-3 char words.
- Vector layer fused with BM25 by reciprocal rank fusion — on whenever an embed
  endpoint is configured (env vars or <root>/.embed.json); embeddings are
  content-addressed so daily appends pay only their new chunks. --no-vector or
  SHMEM_VECTOR=0 forces the BM25-only path.
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
                        (these two, or a <root>/.embed.json, are what turn the vector
                        layer on; .embed.json holds {"base_url","model"} — endpoint
                        only, non-secret. Keys, if any, go in env, never in the tree.)
  SHMEM_VECTOR          0/off forces the BM25-only path, 1/on forces vector on;
                        unset = auto from whether an endpoint is configured
  SHMEM_EMBED_TIMEOUT   seconds to wait for one embed call (default 10; the query
                        path fast-fails on a loaded/down host rather than hanging)
  SHMEM_EMBED_QUERY_MAX max NEW (cache-miss) embeds one query may create (default
                        4); cache hits are free, the backlog defers to the next sync
  SHMEM_EMBED_QUERY_PREFIX
                        instruction prefix prepended to QUERIES only (asymmetric
                        models, e.g. Qwen3-Embedding); empty = bare queries
  SHMEM_VECTOR_MIN      cosine floor for vector hits (default 0.45, calibrated
                        for Qwen3-Embedding-4B with the default prefix —
                        re-calibrate if you change model or prefix)
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


def _embed_cfg():
    # Tree-local embed config: the injected AGENTS.md commands carry no env vars,
    # so "vector on by default" must be discoverable from the memory tree itself.
    # <root>/.embed.json holds {"base_url": ..., "model": ...} — non-secret
    # endpoint info only (this file is shared with every agent that reads the
    # tree; api keys belong in SHMEM_EMBED_BASE_URL-style env vars). Env vars
    # always win. The file is not markdown, so md_files() never indexes it.
    try:
        cfg = json.loads((SM / ".embed.json").read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


_EMBED_CFG = _embed_cfg()
EMBED_BASE = os.environ.get("SHMEM_EMBED_BASE_URL") or _EMBED_CFG.get("base_url", "")
EMBED_MODEL = os.environ.get("SHMEM_EMBED_MODEL") or _EMBED_CFG.get("model", "")
# Official Qwen3-Embedding instruction wording; applied to queries only (see embed()).
QUERY_PREFIX = os.environ.get("SHMEM_EMBED_QUERY_PREFIX",
                              _EMBED_CFG.get("query_prefix",
                                             "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "))
EMBED_MIN = float(os.environ.get("SHMEM_VECTOR_MIN", _EMBED_CFG.get("min", "0.45")))
# Fast-fail: the query path must not hang on a loaded/down embed host (fleet
# lesson: a busy embed host answers in 30+ s or not at all).
EMBED_TIMEOUT = int(os.environ.get("SHMEM_EMBED_TIMEOUT", "10"))
# A query may create at most this many NEW embeddings (cache misses) before
# deferring the rest to the scheduled re-index; cache hits are free and uncapped.
# Without it, a first --vector query after a big journal day embeds every new
# chunk inline (observed: 343 chunks, ~26 min, one blocked eval).
EMBED_QUERY_MAX = int(os.environ.get("SHMEM_EMBED_QUERY_MAX", "4"))


def vector_enabled():
    """The vector layer is ON whenever an endpoint is configured — env or tree
    config. SHMEM_VECTOR=0/off forces the BM25-only path (eval arms, degraded
    embed host); =1/on forces on. Unset: enabled iff base_url+model resolve."""
    flag = os.environ.get("SHMEM_VECTOR", "").strip().lower()
    if flag in ("0", "off", "no", "false"):
        return False
    if flag in ("1", "on", "yes", "true"):
        return True
    return bool(EMBED_BASE and EMBED_MODEL)


MAX_CHUNK_LINES = 30

# Single source of truth for the version: the tag is cut at this value, the MCP
# handshake reports it, and a test pins the handshake to it.
try:
    from . import get_version
except ImportError:  # executed as a bare script (python shmem/search.py), not imported
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from shmem import get_version

__version__ = get_version()

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


SCHEMA_VERSION = 2  # 2: embeddings content-addressed in emb_cache(hash)


def open_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    # Two agents searching a fresh memory index concurrently both run sync() on the
    # same file; without these pragmas the loser of the race gets "database is locked".
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA journal_mode=WAL")
    # The index is derived and disposable (protocol): a db from an older schema
    # is wiped and rebuilt rather than migrated — the same escape hatch that made
    # the corrupt-fts5 incident of 2026-11-26 a shrug instead of an autopsy.
    if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        for t in ("embed", "emb_cache", "fts", "docs", "files"):
            try:
                con.execute(f"DROP TABLE IF EXISTS {t}")
            except sqlite3.OperationalError:
                pass
        con.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
    con.execute("""CREATE TABLE IF NOT EXISTS docs(
        id INTEGER PRIMARY KEY, path TEXT, start_line INT, end_line INT,
        heading TEXT, text TEXT, mtime REAL, sha TEXT, chash TEXT)""")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(tokens, heading, tokenize='unicode61')")
    # Content-addressed embedding cache: the vector is keyed by the sha256 of the
    # exact text sent to the endpoint (text[:1500]), NOT by doc_id. Chunk rowids
    # churn every re-index and paths move during refactors, but the same text
    # earns the same vector forever — a journal append re-embeds its few new
    # chunks instead of the file's whole history (measured: 343 chunks ~26 min
    # re-embedded for one daily append). Cache rows are never dropped by
    # remove_indexed; the corpus churns slowly and 1000 vectors are ~10 MB.
    con.execute("CREATE TABLE IF NOT EXISTS emb_cache(hash TEXT PRIMARY KEY, vec BLOB)")
    con.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, hash TEXT, mtime REAL)")
    return con


def chunk_hash(text):
    return hashlib.sha256(text[:1500].encode("utf-8")).hexdigest()


def remove_indexed(con, path):
    """Drop all index rows for one file path. Returns the number of chunks removed.

    The embedding CACHE stays: vectors belong to texts, not to files."""
    ids = [r[0] for r in con.execute("SELECT id FROM docs WHERE path=?", (path,)).fetchall()]
    if ids:
        con.executemany("DELETE FROM fts WHERE rowid=?", ((i,) for i in ids))
    con.execute("DELETE FROM docs WHERE path=?", (path,))
    return len(ids)


def index_file(con, path, secrets, vector=False, embed_budget=None):
    """(Re-)index one file. embed_budget caps NEW embeds (None = unlimited,
    used by the scheduled/pipeline syncs; queries pass EMBED_QUERY_MAX).
    Returns (chunks_indexed, new_embeds_used)."""
    raw = path.read_bytes()
    h = hashlib.sha256(raw).hexdigest()
    mtime = path.stat().st_mtime
    chunks = list(chunk_file(path, secrets))
    hashes = {i: chunk_hash(text) for i, (_p, _s, _e, _h, text) in enumerate(chunks)}
    have = {r[0] for r in con.execute(
        "SELECT hash FROM emb_cache WHERE hash IN (%s)" % ",".join("?" * len(hashes)),
        tuple(hashes.values())).fetchall()} if hashes else set()
    todo = [i for i, hh in hashes.items() if hh not in have] if vector else []
    # Embed BEFORE touching the db: the embed loop is network-bound, and doing it
    # inside the write transaction holds the sqlite writer lock across every HTTP
    # round-trip (the 2026-11-26 prod incident that starved live search).
    vecs, used = {}, 0
    if todo:
        for i in todo:
            if embed_budget is not None and used >= embed_budget:
                break  # defer the rest to the scheduled vector sync
            v = embed(chunks[i][4])
            if v:
                vecs[i] = v
                used += 1
    con.execute("INSERT OR REPLACE INTO files(path,hash,mtime) VALUES(?,?,?)", (str(path), h, mtime))
    remove_indexed(con, str(path))
    n = 0
    for i, (p, s, e, heading, text) in enumerate(chunks):
        sha = f"{s}-{e}"
        toks = " ".join(tokenize(text))
        if not toks:
            continue
        cur = con.execute("INSERT INTO docs(path,start_line,end_line,heading,text,mtime,sha,chash) VALUES(?,?,?,?,?,?,?,?)",
                          (str(p), s, e, heading, text, mtime, sha, hashes[i]))
        con.execute("INSERT INTO fts(rowid, tokens, heading) VALUES(?,?,?)", (cur.lastrowid, toks, heading))
        n += 1
        if i in vecs:
            con.execute("INSERT OR IGNORE INTO emb_cache(hash, vec) VALUES(?,?)",
                        (hashes[i], struct.pack(f"{len(vecs[i])}f", *vecs[i])))
    return n, used


def md_files():
    # DIGEST.md stays OUT of the index: it is a derived injection artifact that reprints
    # journal/facts chunks; indexing it only returns duplicate pointers to a non-authoritative copy.
    return [p for p in SM.rglob("*.md")
            if ".index" not in p.parts and p.name != "DIGEST.md"]


def sync(con, vector=None, embed_budget=None):
    """Index files that changed and prune files that vanished since the last run.

    Renamed or deleted markdown must not leave ghost chunks behind: search results
    that point at paths which no longer exist are worse than a cache miss.

    vector=None resolves via vector_enabled() — the vector layer rides along
    whenever an embed endpoint is configured, so a plain sync no longer silently
    strips embeds (the pre-0.9 trap: the daily query path quietly deleted the
    vectors built by --rebuild --vector)."""
    on_disk = {str(p) for p in md_files()}
    changed = [p for p in md_files()
               if (row := con.execute("SELECT hash FROM files WHERE path=?", (str(p),)).fetchone()) is None
               or row[0] != hashlib.sha256(p.read_bytes()).hexdigest()]
    gone = [r[0] for r in con.execute("SELECT path FROM files").fetchall() if r[0] not in on_disk]
    if not changed and not gone:
        return 0, 0
    if vector is None:
        vector = vector_enabled()
    secrets = cred_values()
    indexed = 0
    for path in gone:
        remove_indexed(con, path)
        con.execute("DELETE FROM files WHERE path=?", (path,))
    for path in changed:
        n, _used = index_file(con, path, secrets, vector, embed_budget)
        indexed += n
        con.commit()  # per-file: never hold the writer lock across the next file's embeds
    return indexed, len(gone)


def rebuild(con, vector=None):
    """Full rebuild. The emb_cache deliberately SURVIVES it: vectors belong to
    texts, and this is the path that refills whatever the cache lost."""
    if vector is None:
        vector = vector_enabled()
    secrets = cred_values()
    con.execute("DELETE FROM docs"); con.execute("DELETE FROM fts"); con.execute("DELETE FROM files")
    con.commit()  # publish the wipe before the slow part; readers never wait on it
    n = 0
    for path in sorted(md_files()):
        chunks, _used = index_file(con, path, secrets, vector)
        n += chunks
        con.commit()
    return n


def embed(text, query=False):
    # Qwen3-Embedding (the fleet's endpoint) is trained asymmetrically: passages
    # are embedded bare, queries carry an instruction prefix. Embedding queries
    # bare leaves an anisotropy floor — measured 2026-11-26, gibberish queries
    # scored up to 0.52 cosine against real chunks, so no similarity floor can
    # separate them. With the official instruction the same gibberish tops out
    # at 0.39 while real queries keep >= 0.55, which is what EMBED_MIN below
    # calibrates against. The pairing is model-specific: change the model or
    # the prefix and re-calibrate the floor. Set SHMEM_EMBED_QUERY_PREFIX=""
    # for models that want bare queries.
    text = text[:1500]
    if query and QUERY_PREFIX:
        text = QUERY_PREFIX + text
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{EMBED_BASE.rstrip('/')}/embeddings",
            data=json.dumps({"model": EMBED_MODEL, "input": [text]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as r:
            return json.load(r)["data"][0]["embedding"]
    except Exception as e:
        sys.stderr.write(f"embed failed: {e}\n")
        return None


def bm25(con, query, limit):
    toks = tokenize(query)
    if not toks:
        return []
    match = " OR ".join(f'"{t.replace(chr(34), chr(34)+chr(34))}"' for t in toks[:24])
    # ORDER BY path,start_line breaks score ties: fts rowids shift whenever a file is
    # re-indexed, so tie-breaking on rowid would make "is X in the top N" flip between runs.
    rows = con.execute(
        "SELECT f.rowid, bm25(fts, 1.0, 3.0) AS score FROM fts f JOIN docs d ON d.id = f.rowid "
        "WHERE fts MATCH ? ORDER BY score, d.path, d.start_line LIMIT ?",
        (match, limit * 3)).fetchall()
    return [(rid, -sc) for rid, sc in rows]


def vector_recall(con, query, limit):
    if not vector_enabled():
        return []
    v = embed(query, query=True)
    if not v:
        return []
    rows = con.execute("SELECT d.id, c.vec FROM docs d JOIN emb_cache c ON c.hash=d.chash").fetchall()
    na = sum(a * a for a in v) ** .5
    if not na:
        return []
    scored = []
    for doc_id, blob in rows:
        vec = struct.unpack(f"{len(blob) // 4}f", blob)
        if len(vec) != len(v):
            continue
        nb = sum(b * b for b in vec) ** .5
        if nb:
            cos = sum(a * b for a, b in zip(v, vec)) / (na * nb)
            if cos >= EMBED_MIN:  # floor: below it the "match" is anisotropy noise,
                scored.append((doc_id, cos))  # not signal (calibration: see EMBED_MIN)
    scored.sort(key=lambda x: -x[1])
    # Empty result is a feature: search() then falls back to BM25 alone, so a
    # query nothing is close to returns what BM25 honestly has — or nothing.
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


def search(query, limit=8, vector=None, con=None):
    own = con is None
    if own:
        con = open_db()
    try:
        if vector is None:
            vector = vector_enabled()
        # Since 0.9.0 the query path may embed (vectors ride along whenever an
        # endpoint is configured) but is BOUNDED: content-addressed cache means a
        # daily journal append reuses every old chunk's vector and pays only the
        # new chunks, and EMBED_QUERY_MAX caps the new embeds per query so even
        # a first query after a huge day defers the backlog to the scheduled
        # re-index instead of blocking (the 26-minute-eval incident of
        # 2026-11-26 could not recur on this path).
        sync(con, vector, embed_budget=EMBED_QUERY_MAX)
        bm = bm25(con, query, limit)
        vv = vector_recall(con, query, limit) if vector else []
        ids = fuse(bm, vv, limit * 2) if vv else [(rid, 1.0 / (i + 1)) for i, (rid, _s) in enumerate(bm)]
        # Diversity: one chatty file (a journal mentioning the term fifty times) must
        # not crowd facts/inbox out of the window — cap 2 chunks per file, fill the
        # window from other files, then backfill the remainder with the overflow.
        out, overflow, per_file = [], [], {}
        for rid, score in ids:
            row = doc_row(con, rid)
            if not row:
                continue
            path, s, e, heading, text = row
            rec = {"path": path, "lines": [s, e], "heading": heading,
                   "score": round(score, 4), "text": text}
            (out if per_file.get(path, 0) < 2 else overflow).append(rec)
            per_file[path] = per_file.get(path, 0) + 1
        out = out[:limit]
        out.extend(overflow[:limit - len(out)])
        return out
    finally:
        if own:
            con.close()


def stats(con):
    n = con.execute("SELECT count(*) FROM docs").fetchone()[0]
    files = con.execute("SELECT count(DISTINCT path) FROM docs").fetchone()[0]
    emb = con.execute("SELECT count(*) FROM emb_cache").fetchone()[0]
    stale = []
    for p in sorted((SM / "inbox").rglob("*.md")):
        age = (now().timestamp() - p.stat().st_mtime) / 86400
        if age > 3:  # an unread inbox file is a delivery failure waiting to happen
            stale.append({"path": str(p.relative_to(SM)), "age_days": round(age, 1)})
    return {"docs": n, "files": files, "embeddings": emb, "db": str(DB),
            "vector_enabled": bool(EMBED_BASE and EMBED_MODEL), "stale_inbox": stale}


# ---------- Write face (three channels, protocol-enforced; MCP-only, opt-in) ----------
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def msg_slug(content):
    """Filename stem for one inbox message: ascii slug of its first line, or a hash
    when the line has no ascii at all (CJK-only conclusions still need a name)."""
    first = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
    words = re.findall(r"[a-z0-9]+", first.lower())
    stem = "-".join(words)[:40].rstrip("-")
    return stem or hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]


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
            return False, f"agent must be a kebab slug (inbox/TO-<agent>/), got {agent!r}"
        if content.count("\n") + 1 > 20:
            return False, "inbox message must be <= 20 lines"
        # One file per message: a single file per recipient is last-writer-wins, and an
        # unread message silently disappears. The directory is the queue.
        inbox = SM / "inbox" / f"TO-{agent}"
        stem = f"{now():%Y-%m-%d}-{msg_slug(content)}"
        target = inbox / f"{stem}.md"
        if target.exists():
            i = 2  # step to a free suffix: nothing here is ever overwritten
            while (inbox / f"{stem}-{i}.md").exists():
                i += 1
            target = inbox / f"{stem}-{i}.md"
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
    sync(con, embed_budget=EMBED_QUERY_MAX)  # incremental; embed capped like queries
    return True, f"wrote {target.relative_to(SM)}"


# ---------- MCP face (minimal stdio JSON-RPC, no deps) ----------
def mcp_tools(allow_write=False):
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
             "description": "Deliver one message as its own file under inbox/TO-<agent>/ "
                            "(<= 20 lines: conclusion + pointers + ask). Never clobbers an unread message.",
             "inputSchema": {"type": "object", "properties": {"agent": {"type": "string"},
                             "content": {"type": "string"}},
                             "required": ["agent", "content"]}}]
    return tools


def mcp_loop(allow_write=False):
    con = open_db()
    try:
        _mcp_loop(con, allow_write)
    finally:
        con.close()


def _mcp_loop(con, allow_write=False):
    tools = mcp_tools(allow_write)

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
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "shmem", "version": __version__}}})
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue
        elif method == "tools/list":
            reply({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            name = msg.get("params", {}).get("name")
            args = msg.get("params", {}).get("arguments", {})
            if name == "memory_search":
                res = search(args.get("query", ""), int(args.get("limit", 8)), con=con)
                content = json.dumps(res, ensure_ascii=False, indent=1)
            elif name == "memory_stats":
                sync(con, embed_budget=EMBED_QUERY_MAX)
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="shmem query-time recall (BM25 + optional vector)")
    ap.add_argument("query", nargs="?", help="query text (CJK ok)")
    ap.add_argument("-n", "--limit", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--vector", dest="vector", action="store_true", default=None,
                    help="force vector fusion on (endpoint from env or <root>/.embed.json)")
    ap.add_argument("--no-vector", dest="vector", action="store_false",
                    help="force the BM25-only path (also SHMEM_VECTOR=0)")
    ap.add_argument("--rebuild", action="store_true", help="incremental: re-index files changed since last index")
    ap.add_argument("--full", action="store_true", help="drop the index and rebuild from scratch")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--mcp", action="store_true", help="run as stdio MCP server")
    ap.add_argument("--allow-write", action="store_true",
                    help="with --mcp: expose memory_write / memory_append / inbox_write (default face is read-only)")
    a = ap.parse_args(argv)

    if a.mcp:
        mcp_loop(allow_write=a.allow_write)
        return

    con = open_db()
    did = False
    try:
        if a.full:
            n = rebuild(con, vector=a.vector)
            print(f"rebuilt {n} chunks"); did = True
        elif a.rebuild:
            indexed, pruned = sync(con, vector=a.vector)
            print(f"re-indexed {indexed} chunks, pruned {pruned} (changed or deleted files)"); did = True
        if a.stats:
            print(json.dumps(stats(con), ensure_ascii=False, indent=1)); did = True
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
    finally:
        con.close()


if __name__ == "__main__":
    main()
