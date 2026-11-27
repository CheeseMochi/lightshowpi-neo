# LightShowPi Neo - deployment shortcuts. Run `make` for help.
# See deploy/README.md for the full runbook.

ANSIBLE_DIR   := deploy/ansible
TF_DIR        := deploy/terraform/cloudflare
CONDA_DEV_ENV ?= lightshowpi-neo-dev
# Mac-only env for composing light patterns (research/, the pattern lab)
CONDA_LAB_ENV ?= lightshowpi-neo-lab
LAB_PORT      ?= 8765
# Folder with your own songs for the lab (default: the last one used, else ./music)
MUSIC         ?=
HOST          ?=
TOKEN         ?=
ARGS          ?=
WORKER_DIR    := cloud/worker
PUBLIC_HOST   ?=
ADMIN_HOST    ?=
# Home page title on the cloud site (blank = "Holiday Light Show")
SHOW_NAME     ?=
# WWW=1 also attaches www.PUBLIC_HOST to the Worker (WWW=0 to skip)
WWW           ?= 1
# Workers KV namespace id for the public Worker (printed by
# `npx wrangler kv namespace create SHOW_KV`). Pass KV_ID=... or save it once
# in cloud/worker/.kv-id (gitignored), so wrangler.toml stays a clean template.
KV_ID         ?= $(shell cat $(WORKER_DIR)/.kv-id 2>/dev/null)

# ASK_VAULT=1 prompts for the ansible-vault password (if secrets.yml is encrypted)
VAULT_FLAGS := $(if $(ASK_VAULT),--ask-vault-pass,)
PLAYBOOK    := ansible-playbook site.yml $(VAULT_FLAGS)

.DEFAULT_GOAL := help
.PHONY: help deploy-deps provision deploy media runner tunnel tunnel-token test lint-deploy \
        cloud cloud-test dev-env lab-env lab lab-test _require-host _require-inventory

help: ## Show this help
	@echo "LightShowPi Neo deployment"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make provision HOST=lsp-show"
	@echo "  make deploy HOST=show"
	@echo "  make media HOST=piserver-neo SRC=~/backups/piserver OLD_PREFIX=/home/scroce/test/lightshowpi"
	@echo "  make runner HOST=lsp-bench TOKEN=<github-runner-registration-token>"
	@echo "  make cloud PUBLIC_HOST=lightshow.example.com ADMIN_HOST=lights.example.com"
	@echo "  ASK_VAULT=1 make provision HOST=all"

deploy-deps: ## Install Ansible collections needed by the playbook (one time)
	cd $(ANSIBLE_DIR) && ansible-galaxy collection install -r requirements.yml

provision: _require-host _require-inventory ## Full setup of HOST (inventory host or group): packages, conda, app, tunnel/runner
	cd $(ANSIBLE_DIR) && $(PLAYBOOK) --limit '$(HOST)' $(ARGS)

deploy: _require-host _require-inventory ## Update code/config/services only (lightshowpi role) on HOST
	cd $(ANSIBLE_DIR) && $(PLAYBOOK) --limit '$(HOST)' --tags lightshowpi $(ARGS)

media: _require-host _require-inventory ## Copy music/ and config/*.cfg from a local backup (SRC=dir, optional OLD_PREFIX=old install path)
	@test -n "$(SRC)" || { echo "ERROR: SRC=<local backup dir> is required"; exit 1; }
	cd $(ANSIBLE_DIR) && ansible-playbook media.yml $(VAULT_FLAGS) --limit '$(HOST)' -e src='$(abspath $(patsubst ~%,$(HOME)%,$(SRC)))' -e old_prefix='$(OLD_PREFIX)' $(ARGS)

runner: _require-host _require-inventory ## Install/register the GitHub Actions runner on bench HOST (TOKEN=... for first registration)
	@cd $(ANSIBLE_DIR) && GH_RUNNER_TOKEN='$(TOKEN)' $(PLAYBOOK) --limit '$(HOST)' --tags base,gh_runner $(ARGS)

tunnel: ## Create/update the Cloudflare Tunnel, DNS and Access (needs CLOUDFLARE_API_TOKEN)
	@test -n "$$CLOUDFLARE_API_TOKEN" || { echo "ERROR: export CLOUDFLARE_API_TOKEN=... first (see deploy/README.md)"; exit 1; }
	@test -f $(TF_DIR)/terraform.tfvars || { echo "ERROR: copy $(TF_DIR)/terraform.tfvars.example to terraform.tfvars and fill it in"; exit 1; }
	cd $(TF_DIR) && terraform init -input=false && terraform apply

tunnel-token: ## Save the tunnel token for Ansible (group_vars/show/tunnel.yml, git-ignored)
	@umask 077; tok=$$(cd $(TF_DIR) && terraform output -raw tunnel_token) && test -n "$$tok" && \
		printf -- '---\n# Written by make tunnel-token. Do not commit.\nvault_cloudflared_tunnel_token: "%s"\n' "$$tok" \
		> $(ANSIBLE_DIR)/group_vars/show/tunnel.yml
	@echo "Wrote $(ANSIBLE_DIR)/group_vars/show/tunnel.yml (mode 600). Next: make provision HOST=show"

cloud: ## Build the Home page for the cloud and deploy the public Worker (PUBLIC_HOST=... ADMIN_HOST=... [KV_ID=...] [SHOW_NAME="..."] [WWW=0])
	@test -n "$(PUBLIC_HOST)" || { echo "ERROR: set PUBLIC_HOST=<public hostname>, e.g. make cloud PUBLIC_HOST=lightshow.example.com ADMIN_HOST=lights.example.com"; exit 1; }
	@test -n "$(ADMIN_HOST)" || { echo "ERROR: set ADMIN_HOST=<admin hostname>; the gear icon and /admin go to https://ADMIN_HOST/admin"; exit 1; }
	@echo "$(KV_ID)" | grep -qE '^[0-9a-f]{32}$$' || { echo "ERROR: set KV_ID=<KV namespace id> or save it in $(WORKER_DIR)/.kv-id (npx wrangler kv namespace list shows it; deploy/README.md, Cloud public page)"; exit 1; }
	@test -z "$$CLOUDFLARE_API_TOKEN" || echo "NOTE: CLOUDFLARE_API_TOKEN is set, so wrangler uses it instead of your wrangler login. If the deploy fails with 'No access', rerun with: env -u CLOUDFLARE_API_TOKEN make cloud ..."
	cd web && npm ci && VITE_PUBLIC_API_BASE=/api/public VITE_ADMIN_URL=https://$(ADMIN_HOST)/admin VITE_SHOW_NAME='$(SHOW_NAME)' npx vite build --outDir dist-cloud --emptyOutDir
	sed 's/^id = ".*"/id = "$(KV_ID)"/' $(WORKER_DIR)/wrangler.toml > $(WORKER_DIR)/wrangler.deploy.toml
	cd $(WORKER_DIR) && npm ci && npx wrangler deploy --config wrangler.deploy.toml --domain $(PUBLIC_HOST) $(if $(filter 1,$(WWW)),--domain www.$(PUBLIC_HOST),) --var ADMIN_HOST:$(ADMIN_HOST) --var PUBLIC_HOST:$(PUBLIC_HOST)

cloud-test: ## Run the public Worker's tests (vitest in workerd)
	cd $(WORKER_DIR) && npm ci && npm test

dev-env: ## Create or update the dev conda env (environment-dev.yml; mirrors the Pi and CI)
	@if conda env list | grep -qE '^$(CONDA_DEV_ENV)[[:space:]]'; then \
		conda env update -n $(CONDA_DEV_ENV) -f environment-dev.yml --prune; \
	else conda env create -n $(CONDA_DEV_ENV) -f environment-dev.yml; fi

lab-env: ## Create or update the pattern-lab conda env (environment-lab.yml; Mac only, ~2 GB with Demucs)
	@if conda env list | grep -qE '^$(CONDA_LAB_ENV)[[:space:]]'; then \
		conda env update -n $(CONDA_LAB_ENV) -f environment-lab.yml --prune; \
	else conda env create -n $(CONDA_LAB_ENV) -f environment-lab.yml; fi

lab: ## Start the pattern lab GUI on http://127.0.0.1:8765 (MUSIC=~/Music/Christmas, LAB_PORT=...)
	conda run --no-capture-output -n $(CONDA_LAB_ENV) python -m research.lab serve --port $(LAB_PORT) \
		$(if $(MUSIC),--music "$(MUSIC)",) $(ARGS)

lab-test: ## Run the composer and lab tests in the lab env
	SYNCHRONIZED_LIGHTS_HOME=$(CURDIR) conda run -n $(CONDA_LAB_ENV) pytest -q -p no:cacheprovider \
		tests/test_research_composer.py tests/test_research_lab.py

test: ## Run the Python test suite locally (conda env lightshowpi-neo-dev; override CONDA_DEV_ENV=...)
	SYNCHRONIZED_LIGHTS_HOME=$(CURDIR) conda run -n $(CONDA_DEV_ENV) pytest -q

lint-deploy: ## Lint Ansible (syntax-check, ansible-lint) and Terraform (fmt, validate)
	cd $(ANSIBLE_DIR) && ansible-playbook --syntax-check -i inventory.example.yml site.yml
	cd $(ANSIBLE_DIR) && ansible-playbook --syntax-check -i inventory.example.yml media.yml
	cd $(ANSIBLE_DIR) && ansible-lint --offline
	cd $(TF_DIR) && terraform fmt -check -recursive
	cd $(TF_DIR) && terraform init -backend=false -input=false >/dev/null && terraform validate

_require-host:
	@test -n "$(HOST)" || { echo "ERROR: set HOST=<inventory host or group>, e.g. make $(MAKECMDGOALS) HOST=show"; exit 1; }

_require-inventory:
	@test -f $(ANSIBLE_DIR)/inventory.yml || { echo "ERROR: copy $(ANSIBLE_DIR)/inventory.example.yml to inventory.yml and edit it"; exit 1; }
