variable "project_id" { type = string }
variable "service_name" { type = string }
variable "alert_email" { type = string }

variable "max_5xx_per_minute" {
  type    = number
  default = 1
}

variable "max_p95_latency_ms" {
  description = "End to end, including the model call."
  type        = number
  default     = 15000
}
