# Staging bootstrap

One-time setup of the staging environment on Google Cloud. You need a project with billing, `gcloud`, Terraform 1.8 or later, and owner rights on the project.

## 1. State bucket

```bash
gcloud storage buckets create gs://YOUR_PROJECT-tfstate --location=europe-west1 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://YOUR_PROJECT-tfstate --versioning
```

Terraform state holds the generated database passwords, so keep this bucket to the people who run Terraform.

## 2. Create the secrets

The gateway service reads four secrets whose values Terraform never sees. Cloud Run will not create a service whose secrets have no value yet, so create the secrets on their own first:

```bash
cd infra/terraform/envs/staging
cp terraform.tfvars.example terraform.tfvars   # fill in project, repo, email; anchor_first_run = true
terraform init -backend-config="bucket=YOUR_PROJECT-tfstate"
terraform apply -target=module.gateway.google_secret_manager_secret.managed
```

Terraform warns that `-target` is for exceptional cases; this bootstrap is one.

## 3. Secret values

The secrets exist but are empty. Add values (they never pass through Terraform). Write each value without a trailing newline: a key ending in a newline breaks the HTTP header it is sent in.

Bash:

```bash
openssl rand -hex 32 | tr -d '\n' | sed 's/^/sk-/' | gcloud secrets versions add litellm-master-key --data-file=-
printf '%s' "$TYPESAFE_API_KEY"  | gcloud secrets versions add typesafe-api-key --data-file=-
printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-

# Caller keys: mint one per agent, and one for the deploy smoke test.
uv run derva keys new --alias smoke-test --owner you@example.ie --team wealth-a
# Collect the printed entries into a JSON list:
printf '%s' '[{...}, {...}]' | gcloud secrets versions add derva-callers --data-file=-
```

PowerShell (piping text to `gcloud` adds a newline, so this goes through a temporary file):

```powershell
$project = "YOUR_PROJECT"
function Add-Secret([string]$Name, [string]$Value) {
    $file = New-TemporaryFile
    [IO.File]::WriteAllText($file.FullName, $Value)   # no trailing newline, no BOM
    gcloud secrets versions add $Name --data-file="$($file.FullName)" --project $project
    Remove-Item $file
}
function Read-Secret([string]$Prompt) {
    [Net.NetworkCredential]::new("", (Read-Host -AsSecureString $Prompt)).Password
}

$bytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
Add-Secret litellm-master-key ("sk-" + -join ($bytes | ForEach-Object { $_.ToString("x2") }))
Add-Secret anthropic-api-key (Read-Secret "Anthropic API key")
Add-Secret typesafe-api-key (Read-Secret "TypeSafe API key, or not-set")

uv run derva keys new --alias smoke-test --owner you@example.ie --team wealth-a
Add-Secret derva-callers '[{...}, {...}]'   # the printed entries, as a JSON list
```

Without a TypeSafe key yet, `not-set` is safe: the decision-model call fails, the chain falls back to rules, and the circuit breaker stops retrying. Replace it when you have a key. Keep the smoke-test key: it becomes the GitHub secret `DERVA_SMOKE_KEY` in step 5.

## 4. Apply the rest

```bash
terraform apply
```

If an earlier full apply failed on the service and this one reports that it already exists, delete it (`gcloud run services delete derva-gateway-staging --region europe-west1`) and apply again.

## 5. GitHub

From `terraform output github_actions_variables`, add these as repository variables (Settings > Secrets and variables > Actions > Variables): `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`, `GCP_REGISTRY`, `CLOUD_RUN_SERVICE`.

Add the smoke-test key as the secret `DERVA_SMOKE_KEY`, and create an environment called `staging` (add required reviewers there if deploys should wait for approval).

## 6. First deploy

Push to `main`, or run the `deploy-staging` workflow by hand. It builds the image, migrates the schema, deploys a canary with no traffic, tests it and then moves traffic.

## 7. First anchor

Run the anchor job once, then switch off the first-run flag:

```bash
gcloud run jobs execute derva-audit-anchor --region europe-west1 --wait
# then set anchor_first_run = false in terraform.tfvars and apply again
```

From then on it runs hourly. It fails, and writes no anchor, if the chain does not verify. That failure is the alert to investigate.

## Pausing staging

Cloud SQL is nearly all of staging's idle cost; Cloud Run scales to zero. Between sessions, stop the database and pause the anchor schedule (storage and backups are still billed, a few euros a month):

```bash
gcloud sql instances patch derva-staging --activation-policy=NEVER
gcloud scheduler jobs pause derva-audit-anchor-hourly --location europe-west1
```

Start both again before a deploy, because the smoke test needs the database (it takes a minute or two to start):

```bash
gcloud sql instances patch derva-staging --activation-policy=ALWAYS
gcloud scheduler jobs resume derva-audit-anchor-hourly --location europe-west1
```

If a `terraform plan` taken while paused proposes changing the activation policy, it is offering to start the database again. `terraform destroy` brings the cost to zero, at the price of repeating this bootstrap.

Google will not delete database users or databases from a stopped instance, so destroying a paused staging fails on them. Either start the instance first, or drop them from Terraform's state (deleting the instance removes them anyway) and destroy:

```bash
terraform state rm module.database.google_sql_user.owner module.database.google_sql_user.app module.database.google_sql_database.derva
terraform destroy
```
