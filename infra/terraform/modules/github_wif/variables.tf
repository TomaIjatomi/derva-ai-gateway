variable "project_id" { type = string }
variable "region" { type = string }

variable "github_repository" {
  description = "owner/repo allowed to deploy."
  type        = string
}

variable "runtime_service_accounts" {
  description = "Emails of the service accounts the deployer may deploy as."
  type        = list(string)
}
