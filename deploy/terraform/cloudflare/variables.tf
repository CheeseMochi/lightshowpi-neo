variable "account_id" {
  description = "Cloudflare account ID (dashboard sidebar, or `Account Home` > three-dot menu > Copy account ID)."
  type        = string
}

variable "zone" {
  description = "Domain managed in Cloudflare, e.g. example.com."
  type        = string
}

variable "admin_subdomain" {
  description = "Subdomain for the admin UI/API (protected by Cloudflare Access)."
  type        = string
  default     = "lights"
}

variable "public_subdomain" {
  description = "Subdomain for the public show page (no login, restricted paths only)."
  type        = string
  default     = "show"
}

variable "admin_emails" {
  description = "Email addresses allowed through Cloudflare Access to the admin host (one-time PIN login)."
  type        = list(string)

  validation {
    condition     = length(var.admin_emails) > 0
    error_message = "admin_emails must contain at least one address."
  }
}

variable "tunnel_name" {
  description = "Name of the Cloudflare Tunnel."
  type        = string
  default     = "lightshowpi"
}

variable "origin_service" {
  description = "Local origin cloudflared forwards to on the show Pi (the LightShowPi API)."
  type        = string
  default     = "http://localhost:8000"
}

variable "public_path_regexes" {
  description = <<-EOT
    Paths served on the public host. cloudflared treats ingress `path` as an
    unanchored regular expression, so every entry MUST be anchored with ^ (and $
    where an exact match is intended). Anything not matched returns 404.
  EOT
  type        = list(string)
  default = [
    "^/$",
    "^/api/public/",
    "^/assets/",
    "^/public/?$",
  ]

  validation {
    condition     = alltrue([for p in var.public_path_regexes : startswith(p, "^")])
    error_message = "Every public_path_regexes entry must start with ^ (cloudflared paths are unanchored regexes)."
  }
}

variable "create_otp_idp" {
  description = "Create the One-time PIN identity provider. Set false if the account already has one (Cloudflare allows only one)."
  type        = bool
  default     = true
}

variable "access_session_duration" {
  description = "How long an Access login on the admin host stays valid."
  type        = string
  default     = "24h"
}
