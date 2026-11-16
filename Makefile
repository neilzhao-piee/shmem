# shmem — zero-dependency shared memory for agents
#
# Quick start:
#   make new DIR=~/my-memory        scaffold a memory from examples/
#   make search Q="how was X decided"
#   make digest                     regenerate DIGEST.md
#   make index                      (re)build the FTS5 index
#   make summarize                  one manual summarizer run
#   make test                       run the unit tests
#   make install-units DIR=~/my-memory   install the systemd user timer
#
# ROOT defaults to the directory containing this Makefile.

ROOT ?= $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
export SHMEM_ROOT := $(ROOT)
export SHMEM_TZ ?= UTC
PYTHON ?= python3

.PHONY: new search index digest summarize stats test install-units clean

new: ## scaffold a new memory tree from examples/
	@test -n "$(DIR)" || { echo "usage: make new DIR=/path/to/memory"; exit 1; }
	@if [ -e "$(DIR)" ]; then echo "$(DIR) exists"; exit 1; fi
	mkdir -p "$(DIR)"
	cp -r examples/* "$(DIR)/"
	cp -r tools "$(DIR)/tools"
	rm -rf "$(DIR)/tools/__pycache__"
	@echo "memory created at $(DIR)"
	@echo "add SHMEM_ROOT=$(DIR) to your env (see config.example.env)"

search: ## search: make search Q="query"
	@test -n "$(Q)" || (echo "usage: make search Q=\"query\""; exit 1)
	$(PYTHON) $(ROOT)/tools/memory-search.py "$(Q)" -n 8

index: ## incremental re-index of changed files
	$(PYTHON) $(ROOT)/tools/memory-search.py --rebuild

digest: ## regenerate DIGEST.md (<=9KB self-contained snapshot)
	bash $(ROOT)/tools/make-digest.sh

summarize: ## one manual summarizer run (incremental)
	$(PYTHON) $(ROOT)/tools/summarize-sessions.py

stats: ## index stats
	$(PYTHON) $(ROOT)/tools/memory-search.py --stats

test: ## run unit tests (stdlib unittest, CI gate)
	$(PYTHON) $(ROOT)/tests/test_shmem.py

install-units: ## install systemd user timer (DIR = memory root, defaults to ROOT)
	@if ! systemctl --user show-environment >/dev/null 2>&1; then \
	  echo "error: no user systemd instance is reachable (XDG_RUNTIME_DIR=/run/user/$$(id -u) missing?)."; \
	  echo "  Typical cause: SSH session without a login session. Fix: loginctl enable-linger $$USER,"; \
	  echo "  then reconnect. Alternative: system-scope units — see units/README.md"; \
	  exit 1; \
	fi
	@if [ "$$(loginctl show-user "$${USER:-$$(id -un)}" -p Linger 2>/dev/null | sed 's/.*=//')" != "yes" ]; then \
	  echo "error: lingering is off for $${USER:-$$(id -un)} — the timer would only fire while you are"; \
	  echo "  logged in, i.e. it installs successfully and then silently stops. Fix:"; \
	  echo "    loginctl enable-linger $${USER:-$$(id -un)}    # then run make install-units again"; \
	  echo "  Alternative: system-scope units — see units/README.md"; \
	  exit 1; \
	fi
	@dest="$$HOME/.config/systemd/user"; mkdir -p "$$dest"
	@sed "s|@SHMEM_ROOT@|$(or $(DIR),$(ROOT))|g" units/shmem-summarize.service > "$$dest/shmem-summarize.service"
	@sed "s|@SHMEM_ROOT@|$(or $(DIR),$(ROOT))|g" units/shmem-summarize.timer   > "$$dest/shmem-summarize.timer"
	systemctl --user daemon-reload
	systemctl --user enable --now shmem-summarize.timer
	@echo "installed; check with: systemctl --user status shmem-summarize.timer"

clean: ## remove all derived state (safe: everything rebuilds)
	rm -rf $(ROOT)/.index $(ROOT)/.summarize-state.json $(ROOT)/.summarize.log \
	       $(ROOT)/.hook.log $(ROOT)/DIGEST.md
