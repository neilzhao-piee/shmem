"""Pipeline self-check: is the memory pipeline actually alive and honest?

Motivation: the summarizer ran on the keyword fallback for a month while the nightly
run kept logging "run done". Nothing downstream noticed — the journal grew, the digest
injected fallback fragments, and the breakage was invisible unless someone read
.summarize.log. This module turns those signals into one command with an exit code,
and make-digest.sh appends its --oneline output to DIGEST.md so anyone reading the
memory notices a broken pipe without having to go looking.

Config source: when run by the systemd unit the environment carries SUMMARIZE_*;
when run by hand it usually does not, so we also parse the installed unit file
(SHMEM_UNIT, default /etc/systemd/system/shared-memory-summarize.service) — the
place where a misconfigured URL actually lives.
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

UNIT_DEFAULT = "/etc/systemd/system/shared-memory-summarize.service"
OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _root():
    return Path(os.environ.get("SHMEM_ROOT", "."))


def _unit_env():
    """Extract Environment=... assignments from the installed systemd unit."""
    path = os.environ.get("SHMEM_UNIT", UNIT_DEFAULT)
    env = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return env, path
    for m in re.finditer(r"^Environment=(.+)$", text, re.M):
        for tok in m.group(1).split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                env.setdefault(k, v)
    return env, path


def _cfg():
    env, unit = _unit_env()
    # process env wins (live service run); unit file is the fallback for manual runs
    url = os.environ.get("SUMMARIZE_MODEL_URL") or env.get("SUMMARIZE_MODEL_URL", "")
    model = os.environ.get("SUMMARIZE_MODEL") or env.get("SUMMARIZE_MODEL", "")
    return url, model, unit


def check_config(root):
    url, model, unit = _cfg()
    if not url or not model:
        # A tree may deliberately run keyword-only; warn, don't fail.
        return WARN, "model-config", ("SUMMARIZE_MODEL_URL/MODEL not set in env or %s — "
                                      "journal entries will be LOW-QUALITY fallback" % unit)
    if not url.rstrip("/").endswith("/chat/completions"):
        return FAIL, "model-config", ("SUMMARIZE_MODEL_URL=%s is a base URL; the tool POSTs to it "
                                      "verbatim and gets 404 → fallback. Append /chat/completions." % url)
    return OK, "model-config", "url+model set (%s)" % model


def check_model(root):
    url, model, _ = _cfg()
    if not url or not model:
        return WARN, "model-live", "not configured"
    if os.environ.get("SHMEM_HEALTH_PROBE") == "0":
        return WARN, "model-live", "probe skipped (SHMEM_HEALTH_PROBE=0)"
    base = url.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    timeout = int(os.environ.get("SHMEM_HEALTH_TIMEOUT", "25"))
    try:
        req = urllib.request.Request(base + "/models")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ids = [m.get("id") for m in json.loads(resp.read().decode()).get("data", [])]
    except Exception as exc:
        return FAIL, "model-live", "%s unreachable: %s" % (base, exc)
    if model not in ids:
        return FAIL, "model-live", ("model %r not served by %s (ids: %s) — every summarize "
                                    "run will fall back" % (model, base, ", ".join(ids) or "none"))
    # membership passed; one tiny completion proves the chat path end to end
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 4, "temperature": 0}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        return FAIL, "model-live", "completion probe failed on %s: %s" % (url, exc)
    return OK, "model-live", "%s served, completion OK" % model


RUN_DONE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) run done: processed=(\d+) written=(\d+)")
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (dsh|hermes) (\S+) (model|fallback\([^)]*\)|no-model) ")


def check_runs(root):
    log = root / ".summarize.log"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return WARN, "summarize-runs", "no .summarize.log — summariser never ran?"
    last_run = last_model = None
    last_done_processed = None
    for ln in lines:
        m = RUN_DONE_RE.match(ln)
        if m:
            last_run = m.group(1)
            last_done_processed = int(m.group(2))
        m = LINE_RE.match(ln)
        if m and m.group(4).startswith("model"):
            last_model = m.group(1)
    if not last_run:
        return FAIL, "summarize-runs", "log exists but no completed run recorded"
    def days(ts):
        return (time.time() - datetime.strptime(ts, "%Y-%m-%d %H:%M").timestamp()) / 86400.0
    run_age = days(last_run)
    if run_age > 3:
        return FAIL, "summarize-runs", "last run %s (%.0fd ago) — timer is not firing" % (last_run, run_age)
    if not last_model:
        return FAIL, "summarize-runs", "no successful model summary ever recorded in log"
    model_age = days(last_model)
    if model_age > 3:
        return FAIL, "summarize-runs", ("last model-quality summary %s (%.0fd ago) — journal has been "
                                        "growing on fallback only; see last run log lines" % (last_model, model_age))
    if run_age > 1.5:
        return WARN, "summarize-runs", "last run %s (%.1fd ago)" % (last_run, run_age)
    return OK, "summarize-runs", "last run %s, last model summary %.1fd ago" % (last_run, model_age)


def _mtime_days(path):
    try:
        return (time.time() - path.stat().st_mtime) / 86400.0
    except OSError:
        return None


def check_files(root):
    issues = []
    journals = sorted((root / "journal").glob("*.md")) if (root / "journal").is_dir() else []
    if not journals:
        return FAIL, "files", "no journal/*.md"
    jage = _mtime_days(journals[-1])
    if jage is None or jage > 3:
        issues.append("journal %.0fd" % (jage if jage is not None else -1))
    dage = _mtime_days(root / "DIGEST.md")
    if dage is None:
        issues.append("DIGEST.md missing")
    elif dage > 7:
        issues.append("digest %.0fd" % dage)
    stale = [p.name for p in (root / "inbox").glob("TO-*") if (_mtime_days(p) or 0) > 7] if (root / "inbox").is_dir() else []
    if issues:
        return WARN, "files", "; ".join(issues) + ("; stale inbox: %s" % ", ".join(stale) if stale else "")
    if stale:
        return WARN, "files", "stale inbox deliveries: %s" % ", ".join(stale)
    return OK, "files", "journal %.1fd, digest %.1fd" % (jage, dage)


CHECKS = (check_config, check_model, check_runs, check_files)


def run():
    root = _root()
    results = []
    for fn in CHECKS:
        try:
            results.append(fn(root))
        except Exception as exc:  # a broken check must not hide the others
            results.append((WARN, fn.__name__, "check crashed: %s" % exc))
    return results


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    oneline = "--oneline" in argv
    results = run()
    worst = max((s for s, _, _ in results), key=lambda s: (OK, WARN, FAIL).index(s))
    if oneline:
        fails = ["%s: %s" % (n, d) for s, n, d in results if s != OK]
        head = "shmem health: %s (model last OK; see `shmem health`)" % worst if not fails \
            else "shmem health: %s — %s" % (worst, "; ".join(fails))
        sys.stdout.write(head[:280] + "\n")
    else:
        for status, name, detail in results:
            sys.stdout.write("%-4s %-16s %s\n" % (status, name, detail))
        sys.stdout.write("overall: %s\n" % worst)
    return 0 if worst == OK else (2 if worst == FAIL else 1)


if __name__ == "__main__":
    sys.exit(main())
