# Cloud SQL for Postgres 16: the audit log (and LiteLLM's own tables).
# The schema owner applies sql/audit_schema.sql; the gateway logs in as
# derva_app, a member of the insert-only derva_audit_writer role.

resource "google_sql_database_instance" "this" {
  name                = var.instance_name
  project             = var.project_id
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  settings {
    tier              = var.tier
    edition           = "ENTERPRISE"
    availability_type = var.high_availability ? "REGIONAL" : "ZONAL"
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
    }

    ip_configuration {
      # Public IP with no authorised networks: reachable only through the
      # Cloud SQL connector (Cloud Run's /cloudsql socket), which uses IAM.
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }

    database_flags {
      name  = "log_statement"
      value = "ddl"
    }
  }
}

resource "google_sql_database" "derva" {
  name     = "derva"
  project  = var.project_id
  instance = google_sql_database_instance.this.name
}

# Passwords are generated here and stored only in Secret Manager. Rotate by
# tainting the random_password resource.
resource "random_password" "owner" {
  length  = 32
  special = false
}

resource "random_password" "app" {
  length  = 32
  special = false
}

resource "google_sql_user" "owner" {
  name     = "derva_owner"
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  password = random_password.owner.result
}

# The login role. Its membership in derva_audit_writer is granted by the
# migration (sql/audit_schema.sql + `GRANT derva_audit_writer TO derva_app`),
# not here, so the grant is reviewed with the schema.
resource "google_sql_user" "app" {
  name     = "derva_app"
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  password = random_password.app.result
}

locals {
  socket = "/cloudsql/${google_sql_database_instance.this.connection_name}"
}

resource "google_secret_manager_secret" "audit_url" {
  project   = var.project_id
  secret_id = "derva-audit-url"
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
}

resource "google_secret_manager_secret_version" "audit_url" {
  secret      = google_secret_manager_secret.audit_url.id
  secret_data = "postgresql+psycopg://derva_app:${random_password.app.result}@/derva?host=${local.socket}"
}

resource "google_secret_manager_secret" "owner_url" {
  project   = var.project_id
  secret_id = "derva-owner-url"
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
}

resource "google_secret_manager_secret_version" "owner_url" {
  secret      = google_secret_manager_secret.owner_url.id
  secret_data = "postgresql://derva_owner:${random_password.owner.result}@/derva?host=${local.socket}"
}
