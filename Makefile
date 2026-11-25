# LightShowPi Neo - deployment shortcuts. Run `make` for help.
# See deploy/README.md for the full runbook.

ANSIBLE_DIR   := deploy/ansible
TF_DIR        := deploy/terraform/cloudflare
CONDA_DEV_ENV ?= lightshowpi-neo-dev
HOST          ?=
TOKEN         ?=
ARGS          ?=
WORKER_DIR    := cloud/worker
PUBLIC_HOST   ?=
ADMIN_HOST    ?=

# ASK_VAULT=1 prompts for the ansible-vault password (if secrets.yml is encrypted)
VAULT_FLAGS := $(if $(ASK_VAULT),--ask-vault-pass,)
PLAYBOOK    := ansible-playbook site.yml $(VAULT_FLAGS)

.DEFAULT_GOAL := help
.PHONY: help deploy-deps provision deploy media runner tunnel tunnel-token test lint-deploy \
        cloud cloud-test _require-host _require-inventory

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
	@echo "  make cloud PUBLIC_HOST=show.example.com ADMIN_HOST=lights.example.com"
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

cloud: ## Build the Home page for the cloud and deploy the public Worker (PUBLIC_HOST=... ADMIN_HOST=...)
	@test -n "$(PUBLIC_HOST)" || { echo "ERROR: set PUBLIC_HOST=<public hostname>, e.g. make cloud PUBLIC_HOST=show.example.com ADMIN_HOST=lights.example.com"; exit 1; }
	@test -n "$(ADMIN_HOST)" || { echo "ERROR: set ADMIN_HOST=<admin hostname>; the gear icon and /admin go to https://ADMIN_HOST/admin"; exit 1; }
	@! grep -q REPLACE_WITH_KV_NAMESPACE_ID $(WORKER_DIR)/wrangler.toml || { echo "ERROR: create the KV namespace and paste its id into $(WORKER_DIR)/wrangler.toml (deploy/README.md, Cloud public page)"; exit 1; }
	cd web && npm ci && VITE_PUBLIC_API_BASE=/api/public VITE_ADMIN_URL=https://$(ADMIN_HOST)/admin npx vite build --outDir dist-cloud --emptyOutDir
	cd $(WORKER_DIR) && npm ci && npx wrangler deploy --domain $(PUBLIC_HOST) --var ADMIN_HOST:$(ADMIN_HOST) --var PUBLIC_HOST:$(PUBLIC_HOST)

cloud-test: ## Run the public Worker's tests (vitest in workerd)
	cd $(WORKER_DIR) && npm ci && npm test

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
