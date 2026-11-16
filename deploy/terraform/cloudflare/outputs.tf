output "tunnel_id" {
  description = "Cloudflare Tunnel UUID."
  value       = cloudflare_zero_trust_tunnel_cloudflared.lsp.id
}

output "tunnel_token" {
  description = "Connector token for cloudflared on the show Pi (feed to ansible as cloudflared_tunnel_token)."
  value       = data.cloudflare_zero_trust_tunnel_cloudflared_token.lsp.token
  sensitive   = true
}

output "admin_url" {
  value = "https://${local.admin_host}"
}

output "public_url" {
  value = "https://${local.public_host}"
}
