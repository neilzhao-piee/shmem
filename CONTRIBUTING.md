# Contributing

shmem is ~three small tools plus one protocol document. Keep it that way.

## Ground rules

- **Zero third-party dependencies.** Python 3.9+ stdlib and POSIX shell only.
  If a change needs a package, it is the wrong change (the whole point is that
  the memory is a folder anyone can `cat`).
- **POSIX only** (Linux + macOS). Windows is explicitly out of scope: the write
  tools refuse to run there rather than improvise a second locking model.
- **The protocol is the product.** `PROTOCOL.md` is normative. Code that
  enforces protocol rules lives in the reference implementation; anything that
  merely *suggests* protocol behavior belongs in documentation, not in code that
  agents will blame when it misfires.

## Definition of done

Every PR must pass, locally and in CI:

```sh
make test          # stdlib unittest, currently the CI gate
bash eval/run.sh   # recall harness over examples/, 23/23 expected
```

plus the CI extras: `py_compile`, `bash -n`, `shellcheck -S warning`, and
everything under `-W error::ResourceWarning` (no unclosed sqlite connections).

**Protocol changes are a four-part commit**: the `PROTOCOL.md` rule, the
implementation that enforces it, a test that pins it, and both READMEs
(`README.md` + `README.zh.md`) in sync — in one commit, not across a series.
A rule agents won't actually follow is worse than no rule; argue it out in the
PR before writing it.

## Style notes for the tools

- The index (`.index/`) is disposable derived state: correctness must not depend
  on it being warm, and pruning vanished files is a sync() responsibility.
- Journal appends are serialized by `flock` on `.lock` — never write to
  `journal/*.md` outside it; tests exercise this.
- Output budgets (digest 9 KB, injected AGENTS.md slice, ≤20-line inbox) exist
  because agent context is the scarce resource. If you raise a budget, say why
  in the commit message.
- User-visible text stays bilingual-able: keep English output in tools; docs
  carry the Chinese translation in `README.zh.md`.

## Reporting issues

Bugs: include the memory tree shape (file names, sizes) and the exact command.
Retrieval-quality issues ("search missed X") are welcome — paste the query, the
expected file, and what came back instead. Secrets: follow `SECURITY.md`.
