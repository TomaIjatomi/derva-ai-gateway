# Retention-locked bucket for audit chain anchors. Objects cannot be deleted or
# replaced until the retention period ends. Lock the policy only when the period
# is final: a locked policy can never be shortened or removed.

resource "google_storage_bucket" "anchors" {
  name                        = var.bucket_name
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning { enabled = true }

  retention_policy {
    retention_period = var.retention_days * 86400
    is_locked        = var.lock_retention
  }
}

# The anchor job's identity may create objects and nothing else.
resource "google_service_account" "anchor" {
  project      = var.project_id
  account_id   = "derva-anchor"
  display_name = "DERVA audit anchor job"
}

resource "google_storage_bucket_iam_member" "anchor_writer" {
  bucket = google_storage_bucket.anchors.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.anchor.email}"
}

resource "google_storage_bucket_iam_member" "anchor_lister" {
  bucket = google_storage_bucket.anchors.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.anchor.email}"
}

resource "google_secret_manager_secret_iam_member" "anchor_db" {
  project   = var.project_id
  secret_id = var.audit_url_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.anchor.email}"
}

resource "google_project_iam_member" "anchor_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.anchor.email}"
}

# Hourly: verify the chain against the newest anchor, then write a new one.
resource "google_cloud_run_v2_job" "anchor" {
  name                = "derva-audit-anchor"
  project             = var.project_id
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.anchor.email
      max_retries     = 1

      volumes {
        name = "cloudsql"
        cloud_sql_instance { instances = [var.sql_connection_name] }
      }

      containers {
        image   = var.image
        command = ["sh", "-c"]
        args = [
          # A failed check stops the job: never anchor a chain that does not verify.
          "(derva audit verify --anchor gs://${google_storage_bucket.anchors.name}/anchors || [ \"$FIRST_RUN\" = true ]) && derva audit anchor gs://${google_storage_bucket.anchors.name}/anchors"
        ]
        env {
          name = "DERVA_AUDIT_URL"
          value_source {
            secret_key_ref {
              secret  = var.audit_url_secret_id
              version = "latest"
            }
          }
        }
        env {
          # Set to true for the very first run only, when no anchor exists yet.
          name  = "FIRST_RUN"
          value = var.first_run ? "true" : "false"
        }
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }

  lifecycle {
    # The deploy workflow updates the image.
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "derva-anchor-scheduler"
  display_name = "Runs the DERVA anchor job"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.anchor.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "anchor" {
  name     = "derva-audit-anchor-hourly"
  project  = var.project_id
  region   = var.region
  schedule = "7 * * * *"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.anchor.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}
