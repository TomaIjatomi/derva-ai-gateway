# Staging bootstrap

One-time setup of the staging environment on Google Cloud. You need a project with billing, `gcloud`, Terraform 1.8 or later, and owner rights on the project.

## 1. State bucket

```bash
gcloud storage buckets create gs://YOUR_PROJECT-tfstate --location=europe-west1 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://YOUR_PROJECT-tfstate --versioning
```

Terraform state holds the generated database passwords, so keep this bucket to the people who run Terraform.

## 2. Apply

```bash
cd infra/terraform/envs/staging
cp terraform.tfvars.example terraform.tfvars   # fill in project, repo, email; anchor_first_run = true
terraform init -backend-config="bucket=YOUR_PROJECT-tfstate"
terraform apply
```

## 3. Secret values

The secrets exist but are empty. Add values (they never pass through Terraform):

```bash
openssl rand -hex 32 | tr -d '\n' | sed 's/^/sk-/' | gcloud secrets versions add litellm-master-key --data-file=-
printf '%s' "$TYPESAFE_API_KEY"  | gcloud secrets versions add typesafe-api-key --data-file=-
printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-

# Caller keys: mint one per agent, and one for the deploy smoke test.
derva keys new --alias smoke-test --owner you@example.ie --team wealth-a
# Collect the printed entries into a JSON list:
printf '%s' '[{...}, {...}]' | gcloud secrets versions add derva-callers --data-file=-
```

## 4. GitHub

From `terraform output github_actions_variables`, add these as repository variables (Settings > Secrets and variables > Actions > Variables): `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`, `GCP_REGISTRY`, `CLOUD_RUN_SERVICE`.

Add the smoke-test key as the secret `DERVA_SMOKE_KEY`, and create an environment called `staging` (add required reviewers there if deploys should wait for approval).

## 5. First deploy

Push to `main`, or run the `deploy-staging` workflow by hand. It builds the image, migrates the schema, deploys a canary with no traffic, tests it and then moves traffic.

## 6. First anchor

Run the anchor job once, then switch off the first-run flag:

```bash
gcloud run jobs execute derva-audit-anchor --region europe-west1 --wait
# then set anchor_first_run = false in terraform.tfvars and apply again
```

From then on it runs hourly. It fails, and writes no anchor, if the chain does not verify. That failure is the alert to investigate.
