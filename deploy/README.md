# Deploying LightShowPi Neo

Ansible sets up the Pis. Terraform sets up the Cloudflare Tunnel, DNS and Access login.
Wrangler deploys the optional cloud copy of the public Home page (a Cloudflare Worker).
Run everything from your laptop, in the repo root.

```
deploy/
  ansible/              site.yml + roles: base, conda_env, lightshowpi, cloudflared, gh_runner
  terraform/cloudflare/ tunnel, ingress rules, DNS records, Access app + policy
cloud/worker/           Cloudflare Worker: public Home page + cloud copy of /api/public
Makefile                make provision | deploy | runner | tunnel | tunnel-token | test | lint-deploy | cloud
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
make lint-deploy                     # offline check first (terraform validate, ansible-lint)
make tunnel                          # shows the plan; type "yes" to apply
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

## Cloud public page (optional)

Visitors on the street open the Home page from Cloudflare instead of the Pi. The Pi's API pushes
the public data (now playing, show times, playlist titles; nothing else) to a Worker, which keeps
the latest copy in Workers KV and serves the same `/api/public/*` responses plus the built Home
page. Crowds never reach the Pi, and the page keeps working if the tunnel is down.

```
Pi API --(push on change + every 5 min, Bearer key)--> https://<PUBLIC_HOST>/ingest --> KV
phones --> https://<PUBLIC_HOST>/  and  /api/public/{now-playing,schedule,playlist}
           https://<PUBLIC_HOST>/admin --> 302 to https://<ADMIN_HOST>/admin (Access login)
```

If the pushes stop for 15 minutes (the Pi or API is down), now-playing reports `off` rather than
freezing on the last song.

Free-tier budget: each push is one KV write (limit 1,000/day). Heartbeats are 288/day while the
API runs, and each song causes about 3 pushes (title, start time, end), so a 5-hour night of
about 75 songs is roughly 500 writes/day. Every 5 s now-playing poll is one Worker request
(limit 100,000/day; static files don't count), so one phone with the page open costs about
850 requests/hour: roughly 115 phone-hours a day.

**Pick the public hostname.** Any hostname in a zone on your Cloudflare account works, e.g. a
subdomain of the existing zone or a new cheap domain. Only `PUBLIC_HOST` changes. Don't use the
tunnel's public host (`show.<zone>` by default): Terraform owns that DNS record, Wrangler can't
attach a Worker to a hostname that already has one, and deleting it by hand only lasts until the
next `make tunnel`. To move `show` to the Worker, change `public_subdomain` in `terraform.tfvars`
first and re-run `make tunnel`.

One-time setup (laptop, repo root; needs Node 20+):

```bash
cd cloud/worker && npm ci
npx wrangler login                              # opens the browser once
npx wrangler kv namespace create SHOW_KV        # paste the printed id into wrangler.toml
openssl rand -hex 32                            # make the shared key; keep it for the next two steps
npx wrangler secret put INGEST_KEY              # paste the key
cd ../..
make cloud PUBLIC_HOST=lightshow.example.com ADMIN_HOST=lights.example.com
```

`make cloud` builds the Home page into `web/dist-cloud` (with `VITE_PUBLIC_API_BASE=/api/public`
and `VITE_ADMIN_URL=https://<ADMIN_HOST>/admin`; `web/dist`, which goes to the Pi, is not touched)
and runs `wrangler deploy --domain <PUBLIC_HOST>`. Run it again after web changes.

Then on the show Pi, in `config/overrides.cfg`, and restart the API:

```ini
[api]
cloud_url = https://lightshow.example.com
cloud_key = <the same key as INGEST_KEY>
```

Check it during a show: `curl https://lightshow.example.com/api/public/now-playing` should change
within a few seconds of a song change. KV is eventually consistent, so an edge location that
cached the old copy can lag by up to about a minute. About 15 minutes after the API stops, it
should say `off`.

**Turn it off:** blank `cloud_url` in `overrides.cfg` and restart the API. To take the page down,
`cd cloud/worker && npx wrangler delete`.

`make cloud-test` runs the Worker's tests (vitest inside workerd, no account needed).

## Secrets: none are ever committed

| Secret | Where it lives |
|--------|----------------|
| Cloudflare API token | the `CLOUDFLARE_API_TOKEN` env var in your shell |
| Terraform state (includes the tunnel token) | `deploy/terraform/cloudflare/terraform.tfstate` on your laptop (git-ignored); keep a backup |
| Tunnel token | `deploy/ansible/group_vars/show/tunnel.yml` (git-ignored, mode 600), then `/etc/cloudflared/tunnel.env` (0600) on the Pi |
| Admin password | `group_vars/all/secrets.yml` (git-ignored). Optionally `ansible-vault encrypt` it and use `ASK_VAULT=1 make ...` |
| JWT signing key | generated on the first run, stored in `/etc/lightshowpi/api.env` (0600) on the Pi, and reused on later runs |
| Runner registration token | the `TOKEN=` argument, used once; the runner stores its own credentials afterwards |
| Cloud ingest key | Worker secret `INGEST_KEY` (`wrangler secret put`) and `[api] cloud_key` in the Pi's `overrides.cfg` (git-ignored) |

## Notes and gotchas

- **The admin password is used only once.** `ADMIN_PASSWORD` creates the `admin` user only while the
  user database is empty. Changing it later in `secrets.yml` does nothing; change it in the web UI
  (**Password**) or on the Pi with `python -m api.manage set-password admin`.
- **Python version:** the conda env is pinned to the Pi's system `python3` major.minor, so the apt
  `python3-lgpio` C extension can be symlinked in. The play stops with instructions if they differ.
  The env also uses `conda-forge` only, not the `defaults` channel listed in `environment.yml`.
- **API service:** systemd runs `python -m uvicorn api.main:app` on port 8000 directly, because
  `python -m api.main` turns on `reload=True`, which is for development only. `config/overrides.cfg`
  is created only if it is missing and is never overwritten.
- **Web UI:** if `web/dist/index.html` exists on your laptop (`cd web && npm ci && npm run build`),
  `make deploy` copies it to `web/dist` on the Pi. The API does not serve `web/dist` yet; that
  needs app changes before the UI is reachable through port 8000 or the tunnel.
- **Public host:** `show.<domain>` passes only the anchored paths in `public_path_regexes`
  (`^/$`, `^/api/public/`, `^/assets/`, `^/public/?$`) and returns 404 for everything else.
  cloudflared matches these as unanchored Go regexes, which is why each one starts with `^`.
  `lights.<domain>` passes everything, but only after a Cloudflare Access email one-time PIN.
  With a web build on the Pi, `/` is the public Home page and the admin UI is at `/admin`
  (on the admin host). The optional cloud page above can replace the tunnel's public host.
- **Existing One-time PIN login method:** if your Zero Trust account already has one, set
  `create_otp_idp = false` in `terraform.tfvars`.
- **Validate locally:** `make lint-deploy`.
