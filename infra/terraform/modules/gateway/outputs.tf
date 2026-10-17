output "url" { value = google_cloud_run_v2_service.gateway.uri }
output "service_account" { value = google_service_account.gateway.email }
output "service_name" { value = google_cloud_run_v2_service.gateway.name }
