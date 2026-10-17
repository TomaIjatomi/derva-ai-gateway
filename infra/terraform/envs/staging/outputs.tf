output "gateway_url" { value = module.gateway.url }
output "registry" { value = module.github_wif.registry }
output "anchor_bucket" { value = module.anchor.bucket }

# Paste these into the repository's Actions variables (Settings > Secrets and variables).
output "github_actions_variables" {
  value = {
    GCP_PROJECT_ID    = var.project_id
    GCP_REGION        = var.region
    GCP_WIF_PROVIDER  = module.github_wif.provider_name
    GCP_DEPLOYER_SA   = module.github_wif.deployer_email
    GCP_REGISTRY      = module.github_wif.registry
    CLOUD_RUN_SERVICE = module.gateway.service_name
  }
}
