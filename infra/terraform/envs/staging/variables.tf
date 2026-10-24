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

  validation {
    condition = alltrue([
      for m in var.invoker_members : can(regex("^(user|group|serviceAccount|domain):", m))
    ])
    error_message = "Each invoker needs its type in front, e.g. \"user:you@example.ie\" or \"group:team@example.ie\"."
  }
}

variable "anchor_first_run" {
  type    = bool
  default = false
}
