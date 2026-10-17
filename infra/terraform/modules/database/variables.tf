variable "project_id" { type = string }
variable "region" { type = string }
variable "instance_name" { type = string }

variable "tier" {
  type    = string
  default = "db-custom-1-3840"
}

variable "high_availability" {
  type    = bool
  default = false
}

variable "deletion_protection" {
  type    = bool
  default = true
}
