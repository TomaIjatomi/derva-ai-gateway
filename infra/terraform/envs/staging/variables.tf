variable "project_id" { type = string }

variable "region" {
  type    = string
  default = "europe-west1" # Belgium; europe-west2 (London) also suits Irish teams
}

variable "github_repository" {
  description = "owner/repo, e.g. toma-ijatomi/derva-ai-gateway"
  type        = string
}

variable "image" {
  description = "Initial image. Later images come from the deploy workflow."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "alert_email" { type = string }

variable "invoker_members" {
  description = "Who may call the gateway, e.g. [\"user:you@example.ie\"]."
  type        = list(string)
  default     = []
}

variable "anchor_first_run" {
  type    = bool
  default = false
}
