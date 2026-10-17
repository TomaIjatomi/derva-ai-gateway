# Keyless deploys from GitHub Actions: Workload Identity Federation, limited to
# one repository and its main branch. No service account keys exist.

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == \"${var.github_repository}\" && assertion.ref == \"refs/heads/main\""

  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "derva-deployer"
  display_name = "DERVA deploys from GitHub Actions"
}

resource "google_service_account_iam_member" "wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "derva"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions { keep_count = 20 }
  }
}

resource "google_artifact_registry_repository_iam_member" "push" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(["roles/run.developer", "roles/run.invoker"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# Deploying a service that runs as another service account needs actAs on it.
resource "google_service_account_iam_member" "act_as" {
  for_each           = toset(var.runtime_service_accounts)
  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}
