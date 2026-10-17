variable "project_id" { type = string }
variable "region" { type = string }
variable "service_name" { type = string }
variable "image" { type = string }
variable "sql_connection_name" { type = string }

variable "plain_env" {
  description = "Non-secret environment variables."
  type        = map(string)
  default     = {}
}

variable "secret_names" {
  description = "Secrets this module creates (values added out of band). Env name = upper-cased secret id."
  type        = list(string)
  default     = []
}

variable "extra_secret_env" {
  description = "Env name => existing secret id, for secrets created elsewhere."
  type        = map(string)
  default     = {}
}

variable "invoker_members" {
  description = "Principals allowed to call the service, e.g. group:wealth-agents@example.ie."
  type        = list(string)
  default     = []
}

variable "min_instances" {
  type    = number
  default = 1
}

variable "max_instances" {
  type    = number
  default = 4
}

variable "deletion_protection" {
  type    = bool
  default = true
}
