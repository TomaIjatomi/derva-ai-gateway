output "connection_name" { value = google_sql_database_instance.this.connection_name }
output "audit_url_secret_id" { value = google_secret_manager_secret.audit_url.secret_id }
output "owner_url_secret_id" { value = google_secret_manager_secret.owner_url.secret_id }
