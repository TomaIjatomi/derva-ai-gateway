# The gateway on Cloud Run: LiteLLM proxy with the DERVA hooks.
# Invocation needs IAM (no allUsers); callers also need a LiteLLM key.

resource "google_service_account" "gateway" {
  project      = var.project_id
  account_id   = "derva-gateway"
  display_name = "DERVA gateway runtime"
}

resource "google_project_iam_member" "sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

# Provider keys and the master key are created empty here; their values are
# added out of band (gcloud secrets versions add), so they never sit in state.
resource "google_secret_manager_secret" "managed" {
  for_each  = toset(var.secret_names)
  project   = var.project_id
  secret_id = each.value
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
}

locals {
  secret_env = merge(
    { for name, id in var.extra_secret_env : name => id },
    { for name in var.secret_names : upper(replace(name, "-", "_")) => google_secret_manager_secret.managed[name].secret_id },
  )
}

resource "google_secret_manager_secret_iam_member" "gateway" {
  for_each  = local.secret_env
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_cloud_run_v2_service" "gateway" {
  name                = var.service_name
  project             = var.project_id
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection
  # One audience for the service and its tagged canary URLs, so CI can mint a
  # single identity token for both.
  custom_audiences = [var.service_name]

  template {
    service_account                  = google_service_account.gateway.email
    max_instance_request_concurrency = 40
    timeout                          = "300s"

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance { instances = [var.sql_connection_name] }
    }

    containers {
      image = var.image

      resources {
        limits = { cpu = "2", memory = "4Gi" } # spaCy's large model needs the memory
        # Keep CPU between requests so the startup model load is not repeated.
        cpu_idle          = false
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = var.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      startup_probe {
        http_get { path = "/health/liveliness" }
        initial_delay_seconds = 10
        period_seconds        = 5
        failure_threshold     = 24
      }

      liveness_probe {
        http_get { path = "/health/liveliness" }
        period_seconds = 30
      }
    }
  }

  lifecycle {
    # The deploy workflow rolls out new images and moves traffic.
    ignore_changes = [template[0].containers[0].image, traffic, client, client_version]
  }
}

resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invoker_members)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  member   = each.value
}
