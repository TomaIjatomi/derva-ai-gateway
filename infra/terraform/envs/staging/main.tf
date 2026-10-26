locals {
  services = [
    "run.googleapis.com", "sqladmin.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "cloudscheduler.googleapis.com", "monitoring.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

module "database" {
  source              = "../../modules/database"
  project_id          = var.project_id
  region              = var.region
  instance_name       = "derva-staging"
  deletion_protection = false # staging only
  # Shared core, about a fifth of the module default's idle cost. No SLA and a
  # limit of 25 connections, which is why the gateway is capped at 2 instances.
  tier       = "db-f1-micro"
  depends_on = [google_project_service.enabled]
}

module "gateway" {
  source              = "../../modules/gateway"
  project_id          = var.project_id
  region              = var.region
  service_name        = "derva-gateway-staging"
  image               = var.image
  sql_connection_name = module.database.connection_name
  invoker_members     = var.invoker_members
  min_instances       = 0
  max_instances       = 2 # keeps database connections within db-f1-micro's limit
  deletion_protection = false

  plain_env = {
    DERVA_LITELLM_CONFIG    = "config/litellm.yaml"
    DERVA_MASKER            = "presidio"
    DERVA_DECIDER_MODEL     = "typesafe/jev-1.13.0" # adopted 26 Oct 2026, results/DECISIONS.md
    DERVA_SMALL_UPSTREAM    = "anthropic/claude-haiku-5-5"
    DERVA_FRONTIER_UPSTREAM = "anthropic/claude-sonnet-5-5"
  }

  # Created empty; add values with `gcloud secrets versions add`. Cloud Run will
  # not create the service until each has a value, so the runbook creates these
  # first with -target, adds the values, then applies the rest.
  secret_names = ["litellm-master-key", "derva-callers", "typesafe-api-key", "anthropic-api-key"]

  extra_secret_env = {
    DERVA_AUDIT_URL = module.database.audit_url_secret_id
  }
  depends_on = [google_project_service.enabled]
}

module "anchor" {
  source              = "../../modules/anchor"
  project_id          = var.project_id
  region              = var.region
  bucket_name         = "${var.project_id}-derva-anchors-staging"
  image               = var.image
  sql_connection_name = module.database.connection_name
  audit_url_secret_id = module.database.audit_url_secret_id
  retention_days      = 30 # staging; production uses the agreed retention period
  lock_retention      = false
  first_run           = var.anchor_first_run
  depends_on          = [google_project_service.enabled]
}

# Applies sql/audit_schema.sql as the schema owner. The deploy workflow runs it
# before each rollout; it is idempotent.
resource "google_service_account" "migrate" {
  project      = var.project_id
  account_id   = "derva-migrate"
  display_name = "DERVA schema migrations"
}

resource "google_secret_manager_secret_iam_member" "migrate_owner_url" {
  project   = var.project_id
  secret_id = module.database.owner_url_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.migrate.email}"
}

resource "google_project_iam_member" "migrate_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.migrate.email}"
}

resource "google_cloud_run_v2_job" "migrate" {
  name                = "derva-migrate"
  project             = var.project_id
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.migrate.email
      max_retries     = 0
      volumes {
        name = "cloudsql"
        cloud_sql_instance { instances = [module.database.connection_name] }
      }
      containers {
        image   = var.image
        command = ["derva", "audit", "migrate"]
        env {
          name = "DERVA_OWNER_URL"
          value_source {
            secret_key_ref {
              secret  = module.database.owner_url_secret_id
              version = "latest"
            }
          }
        }
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}

module "github_wif" {
  source            = "../../modules/github_wif"
  project_id        = var.project_id
  region            = var.region
  github_repository = var.github_repository
  # Built from account ids, not resource outputs: for_each needs values known at plan.
  runtime_service_accounts = [
    "derva-gateway@${var.project_id}.iam.gserviceaccount.com",
    "derva-migrate@${var.project_id}.iam.gserviceaccount.com",
    "derva-anchor@${var.project_id}.iam.gserviceaccount.com",
  ]
  depends_on = [google_project_service.enabled, module.gateway, module.anchor, google_service_account.migrate]
}

# The deployer also updates and runs the two jobs.
resource "google_cloud_run_v2_job_iam_member" "deployer_jobs" {
  for_each   = toset([google_cloud_run_v2_job.migrate.name, "derva-audit-anchor"])
  project    = var.project_id
  location   = var.region
  name       = each.value
  role       = "roles/run.developer"
  member     = "serviceAccount:${module.github_wif.deployer_email}"
  depends_on = [module.anchor]
}

module "monitoring" {
  source       = "../../modules/monitoring"
  project_id   = var.project_id
  service_name = module.gateway.service_name
  alert_email  = var.alert_email
}
