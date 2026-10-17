# Alerts on the signals the canary also watches: server errors and latency.

resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "DERVA on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_alert_policy" "errors" {
  project      = var.project_id
  display_name = "DERVA gateway: 5xx responses"
  combiner     = "OR"

  conditions {
    display_name = "5xx rate above threshold"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${var.service_name}\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.max_5xx_per_minute
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "latency" {
  project      = var.project_id
  display_name = "DERVA gateway: p95 latency"
  combiner     = "OR"

  conditions {
    display_name = "p95 request latency above threshold"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${var.service_name}\" AND metric.type = \"run.googleapis.com/request_latencies\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.max_p95_latency_ms
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_PERCENTILE_95"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}
