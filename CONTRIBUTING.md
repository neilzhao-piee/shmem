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
bash eval/run.sh   # recall harness over examples/, 25/25 expected
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

## Releasing

A release is five steps, and the last one has been forgotten four times
(v0.5.0–v0.8.0 shipped as git tags only, so the repo's Releases panel sat on
v0.4.0 for a week while `main` moved on — a tag is not a release, GitHub's
UI and `/releases/latest` API only know the latter):

1. Bump `__version__` in `shmem/__init__.py` (single source of truth;
   `pyproject.toml` reads it dynamically — never duplicate the number).
2. Add the `CHANGELOG.md` section for the version (Fixed / Changed / Added /
   Measured, each claim carrying its evidence — production incident, test
   name, or measured numbers).
3. One `release: X.Y.Z — <summary>` commit, after the definition-of-done gates.
4. Annotated tag with a summary of what changed and why:
   `git tag -a vX.Y.Z -m "..."` and push the tag.
5. **Create the GitHub Release** from the tag, with the CHANGELOG section as
   the body — this is what users actually see:
   ```sh
   v=0.9.0   # the version without the leading v
   notes=$(awk "/^## \[$v\]/{f=1;print;next} f&&/^## \[/{exit} f" CHANGELOG.md)
   gh release create "v$v" --title "v$v" --notes "$notes"
   ```

Verify with `gh release view` or the `/releases/latest` API before closing
the release work — the panel, not the tag list, is the published surface.

## Reporting issues

Bugs: include the memory tree shape (file names, sizes) and the exact command.
Retrieval-quality issues ("search missed X") are welcome — paste the query, the
expected file, and what came back instead. Secrets: follow `SECURITY.md`.
