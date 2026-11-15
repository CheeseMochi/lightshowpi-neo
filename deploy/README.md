# Deploying LightShowPi Neo

Ansible sets up the Pis. Terraform sets up the Cloudflare Tunnel, DNS and Access login.
Run everything from your laptop, in the repo root.

```
deploy/
  ansible/              site.yml + roles: base, conda_env, lightshowpi, cloudflared, gh_runner
  terraform/cloudflare/ tunnel, ingress rules, DNS records, Access app + policy
Makefile                make provision | deploy | runner | tunnel | tunnel-token | test | lint-deploy
```

## Prerequisites (one time)

- **Pis:** Raspberry Pi OS **64-bit** (Bookworm or Trixie), with SSH key login for the user in your
  inventory (default `pi`) and passwordless sudo, which is the Pi OS default.
- **Laptop:** `pip install ansible-core ansible-lint` (or `brew install ansible ansible-lint`) and
  `brew install terraform`, then `make deploy-deps` to install the `ansible.posix` collection.
- **Cloudflare:** the domain is already a zone in your account. Create an API token at
  dash.cloudflare.com > My Profile > API Tokens with these permissions:
  - Account > **Cloudflare Tunnel** > Edit
  - Account > **Access: Apps and Policies** > Edit
  - Account > **Access: Organizations, Identity Providers, and Groups** > Edit (only needed to create the One-time PIN login)
  - Zone > **DNS** > Edit (this also covers looking up the zone by name)

## From a fresh Pi to running

```bash
# 1. Describe your Pis and set the admin password (both files are git-ignored)
cp deploy/ansible/inventory.example.yml deploy/ansible/inventory.yml        # edit hosts
cp deploy/ansible/secrets.example.yml   deploy/ansible/group_vars/all/secrets.yml  # set the password

# 2. Create the tunnel, DNS and Access login, then save the tunnel token for Ansible
cp deploy/terraform/cloudflare/terraform.tfvars.example deploy/terraform/cloudflare/terraform.tfvars  # edit
export CLOUDFLARE_API_TOKEN=...      # set in your shell only, never in a file
make tunnel
make tunnel-token

# 3. Provision everything (packages, Miniforge + env, app, systemd, cloudflared)
make provision HOST=all

# 4. Bench Pi only: register the GitHub Actions runner. The token comes from
#    GitHub > Settings > Actions > Runners > New self-hosted runner and is valid for about an hour.
make runner HOST=lsp-bench TOKEN=AAAA...

# Later code updates (git pull + config + restart):
make deploy HOST=show
```

`make` with no target prints the help. Pass extra ansible flags with `ARGS`, for example
`make provision HOST=show ARGS="--check --diff"`.

What runs where:

| Group  | Roles |
|--------|-------|
| show   | base, conda_env, lightshowpi, cloudflared |
| server, client | base, conda_env, lightshowpi |
| bench  | base, gh_runner (its runner labels are `self-hosted, Linux, ARM64, pi-bench`; lightshow crontab entries are commented out) |

## Secrets: none are ever committed

| Secret | Where it lives |
|--------|----------------|
| Cloudflare API token | the `CLOUDFLARE_API_TOKEN` env var in your shell |
| Terraform state (includes the tunnel token) | `deploy/terraform/cloudflare/terraform.tfstate` on your laptop (git-ignored); keep a backup |
| Tunnel token | `deploy/ansible/group_vars/show/tunnel.yml` (git-ignored, mode 600), then `/etc/cloudflared/tunnel.env` (0600) on the Pi |
| Admin password | `group_vars/all/secrets.yml` (git-ignored). Optionally `ansible-vault encrypt` it and use `ASK_VAULT=1 make ...` |
| JWT signing key | generated on the first run, stored in `/etc/lightshowpi/api.env` (0600) on the Pi, and reused on later runs |
| Runner registration token | the `TOKEN=` argument, used once; the runner stores its own credentials afterwards |

## Notes and gotchas

- **The admin password is used only once.** `ADMIN_PASSWORD` creates the `admin` user only while the
  user database is empty. Changing it later in `secrets.yml` does nothing; change it in the web UI.
  Also note that the API currently logs that initial password to the journal.
- **Python version:** the conda env is pinned to the Pi's system `python3` major.minor, so the apt
  `python3-lgpio` C extension can be symlinked in. The play stops with instructions if they differ.
  The env also uses `conda-forge` only, not the `defaults` channel listed in `environment.yml`.
- **API service:** systemd runs `python -m uvicorn api.main:app` on port 8000 directly, because
  `python -m api.main` turns on `reload=True`, which is for development only. `config/overrides.cfg`
  is created only if it is missing and is never overwritten.
- **Web UI:** if `web/dist/index.html` exists on your laptop (`cd web && npm ci && npm run build`),
  `make deploy` copies it to the Pi.
- **Public host:** `show.<domain>` passes only the anchored paths in `public_path_regexes`
  (`^/$`, `^/api/public/`, `^/assets/`, `^/public/?$`) and returns 404 for everything else.
  `lights.<domain>` passes everything, but only after a Cloudflare Access email one-time PIN.
- **Existing One-time PIN login method:** if your Zero Trust account already has one, set
  `create_otp_idp = false` in `terraform.tfvars`.
- **Validate locally:** `make lint-deploy`.
