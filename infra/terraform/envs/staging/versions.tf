terraform {
  required_version = ">= 1.8"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State holds generated database passwords: keep the bucket private,
  # versioned and limited to the people who run Terraform.
  backend "gcs" {
    prefix = "derva/staging"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
