# Cloudflare Tunnel + Access for LightShowPi Neo (provider v5 schema).
#
#   <admin_subdomain>.<zone>  -> whole API/UI, behind Cloudflare Access (email OTP)
#   <public_subdomain>.<zone> -> only public paths, everything else 404

locals {
  admin_host  = "${var.admin_subdomain}.${var.zone}"
  public_host = "${var.public_subdomain}.${var.zone}"
}

data "cloudflare_zone" "this" {
  filter = {
    name = var.zone
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "lsp" {
  account_id = var.account_id
  name       = var.tunnel_name
  config_src = "cloudflare"
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "lsp" {
  account_id = var.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lsp.id
}

# Rule order matters: first match wins.
#   1. public host, allowed paths -> origin
#   2. public host, anything else -> 404
#   3. admin host, all paths      -> origin (Access enforces login at the edge)
#   4. catch-all                  -> 404 (required last rule, no hostname)
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "lsp" {
  account_id = var.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lsp.id

  config = {
    ingress = concat(
      [for p in var.public_path_regexes : {
        hostname = local.public_host
        path     = p
        service  = var.origin_service
      }],
      [
        {
          hostname = local.public_host
          service  = "http_status:404"
        },
        {
          hostname = local.admin_host
          service  = var.origin_service
        },
        {
          service = "http_status:404"
        },
      ],
    )
  }
}

resource "cloudflare_dns_record" "admin" {
  zone_id = data.cloudflare_zone.this.id
  name    = local.admin_host
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.lsp.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "LightShowPi admin (managed by Terraform)"
}

resource "cloudflare_dns_record" "public" {
  zone_id = data.cloudflare_zone.this.id
  name    = local.public_host
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.lsp.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "LightShowPi public show page (managed by Terraform)"
}

# --- Cloudflare Access (admin host only) -------------------------------------

resource "cloudflare_zero_trust_access_identity_provider" "otp" {
  count      = var.create_otp_idp ? 1 : 0
  account_id = var.account_id
  name       = "One-time PIN"
  type       = "onetimepin"
  config     = {}
}

resource "cloudflare_zero_trust_access_policy" "admins" {
  account_id       = var.account_id
  name             = "LightShowPi admins"
  decision         = "allow"
  session_duration = var.access_session_duration
  include          = [for e in var.admin_emails : { email = { email = e } }]
}

resource "cloudflare_zero_trust_access_application" "admin" {
  account_id       = var.account_id
  name             = "LightShowPi admin"
  type             = "self_hosted"
  domain           = local.admin_host
  session_duration = var.access_session_duration

  # With our own OTP IdP, skip the IdP picker. Otherwise allow all account IdPs.
  allowed_idps              = var.create_otp_idp ? [cloudflare_zero_trust_access_identity_provider.otp[0].id] : null
  auto_redirect_to_identity = var.create_otp_idp

  policies = [{
    id         = cloudflare_zero_trust_access_policy.admins.id
    precedence = 1
  }]
}
