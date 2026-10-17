variable "project_id" { type = string }
variable "region" { type = string }
variable "bucket_name" { type = string }
variable "image" { type = string }
variable "sql_connection_name" { type = string }
variable "audit_url_secret_id" { type = string }

variable "retention_days" {
  type    = number
  default = 2557 # seven years
}

variable "lock_retention" {
  description = "Irreversible. Leave false until the retention period is agreed."
  type        = bool
  default     = false
}

variable "first_run" {
  description = "True only until the first anchor exists."
  type        = bool
  default     = false
}
