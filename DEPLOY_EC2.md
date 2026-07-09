# CANASK deployment runbook (AWS EC2 + Secrets Manager)

Same stack as the Lightsail runbook (nginx + web + worker + beat + Postgres + Redis, Docker Compose,
behind Cloudflare) — but on **EC2**, which unlocks the two things Lightsail can't do:

1. **IAM instance role** → the box authenticates to AWS via short-lived, auto-rotating credentials, so
   there are **no static `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` on disk**.
2. **A real secrets manager** (AWS Secrets Manager or SSM Parameter Store) → app secrets live encrypted,
   audited (CloudTrail), and rotatable, instead of in a plaintext `.env.prod`.

Also gained: proper **security groups** (real source-IP filtering) and **encrypted EBS** volumes.

> This doc covers only what's **different** from Lightsail. For the shared steps — Docker install,
> cloning the repo, TLS certs, Cloudflare settings, first launch, verification, updates, rollback — follow
> **[DEPLOY_LIGHTSAIL.md](DEPLOY_LIGHTSAIL.md)**; they're identical once the box and secrets exist.

The security posture is also tracked in `LAUNCH_TODO.md §0` (the Lightsail note there points here for the
IAM-role upgrade).

---

## 1. Provision the instance

- **AMI**: Ubuntu 24.04 LTS. **Type**: `t3.small` (2 GB) minimum, `t3.medium` (4 GB) if Postgres + the
  worker are tight.
- **EBS root volume**: enable **Encryption** (KMS) at launch — encryption at rest for the DB volume.
- **Elastic IP**: allocate and associate one (the stable public IP for Cloudflare DNS).
- Everything from `DEPLOY_LIGHTSAIL.md §3` (install Docker) onward is the same.

## 2. IAM instance role (the main win)

Create an IAM **role** for EC2 and attach it to the instance as an instance profile. Grant only what the
app needs — SES send, reading its own secret, and writing backups:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SendEmail",
      "Effect": "Allow",
      "Action": ["ses:SendEmail", "ses:SendRawEmail"],
      "Resource": "*",
      "Condition": {"StringEquals": {"ses:FromAddress": "no-reply@your-domain.ca"}}
    },
    {
      "Sid": "ReadSecrets",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:canask/prod-*"
    },
    {
      "Sid": "WriteBackups",
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::your-backup-bucket/canask/*"
    }
  ]
}
```

With this role attached, `boto3` and the `aws` CLI pick up credentials automatically from instance
metadata. **No code change is needed** — `data_viz/email.py` already passes
`aws_access_key_id=os.environ.get(...)`, and when those env vars are simply **unset**, boto3 falls
through to the instance-role credential chain. So in `.env.prod` you just leave `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` **blank** and keep `AWS_REGION` set. Same for `deploy/backup.sh` — drop the
`aws configure` step from the Lightsail runbook; the role covers the S3 upload.

> If you encrypt the secret/params with a **customer-managed KMS key**, add `kms:Decrypt` on that key to
> the role. The AWS-managed keys (`aws/secretsmanager`, `aws/ssm`) don't need an explicit `kms:Decrypt`.

## 3. Store the secrets

Two good options — pick one:

**A. SSM Parameter Store (SecureString)** — cheapest (standard params are free), simplest. Store one
param per key under a path:
```
aws ssm put-parameter --type SecureString --name /canask/prod/SECRET_KEY        --value '...'
aws ssm put-parameter --type SecureString --name /canask/prod/INVITE_JWT_SECRET --value '...'
aws ssm put-parameter --type SecureString --name /canask/prod/DB_PASSWORD       --value '...'
# ...one per secret key. Non-secret config (AWS_REGION, PUBLIC_BASE_URL, DEBUG=false) can be plain String.
```
(Grant `ssm:GetParametersByPath` on `/canask/prod/*` instead of the `secretsmanager` statement above.)

**B. AWS Secrets Manager** — one JSON secret, supports built-in rotation (worth it if you'll rotate the
DB password automatically):
```
aws secretsmanager create-secret --name canask/prod \
  --secret-string '{"SECRET_KEY":"...","INVITE_JWT_SECRET":"...","DB_PASSWORD":"...","REDIS_PASSWORD":"...", "...":"..."}'
```

## 4. Render `.env.prod` from the secret at deploy time

Compose still reads `app_config/.env.prod`, so the deploy fetches the secret and writes that file
(chmod 600) fresh on each deploy. The file is ephemeral, regenerated, and never in git or backups — the
**source of truth is the secrets manager**. A small fetch script (run before `make prod-up`):

```bash
#!/usr/bin/env bash
# deploy/fetch-secrets.sh — render app_config/.env.prod from AWS (instance role provides creds).
set -euo pipefail
OUT=app_config/.env.prod

# --- Secrets Manager (option B) ---
aws secretsmanager get-secret-value --secret-id canask/prod --query SecretString --output text \
  | jq -r 'to_entries[] | "\(.key)=\(.value)"' > "$OUT"

# --- OR SSM Parameter Store (option A) ---
# aws ssm get-parameters-by-path --path /canask/prod/ --recursive --with-decryption \
#   --query 'Parameters[].[Name,Value]' --output text \
#   | sed 's|/canask/prod/||' | awk '{printf "%s=%s\n", $1, $2}' > "$OUT"

# Non-secret runtime config the app also needs:
{ echo "DEBUG=false"; echo "AWS_REGION=REGION"; echo "PUBLIC_BASE_URL=https://your-domain"; } >> "$OUT"
chmod 600 "$OUT"
echo "wrote $OUT"
```

Deploy becomes: `./deploy/fetch-secrets.sh && make prod-up`. (I can materialize this script into the repo
if you commit to EC2 — say the word.)

> **Even further — ECS/Fargate**: if you'd rather have *no* secret file on the host at all, run the same
> images on ECS/Fargate and use the task definition's native `secrets:` block to inject Secrets Manager /
> SSM values straight into container environment variables. That removes the `.env.prod` render step and
> the host entirely, at the cost of an ECS task/service definition instead of Docker Compose. It's the
> logical next step if you outgrow a single box.

## 5. Security groups (replaces the Lightsail firewall)

EC2 security groups filter at the ENI, before the instance — so unlike ufw-on-Docker, they actually
restrict the container-published ports:
- **22 (SSH)**: source = your IP only.
- **80 / 443**: source = Cloudflare's ranges. Create a **customer-managed prefix list** from
  https://www.cloudflare.com/ips/ and reference it in the SG rule (one rule, easy to keep updated),
  rather than pasting ~15 CIDRs. mTLS (Authenticated Origin Pulls) remains the real gate; the SG is
  defense-in-depth.

## 6. Everything else

Docker install, repo clone (deploy key), TLS material in `deploy/nginx/tls/`, Cloudflare Full-strict +
Authenticated Origin Pulls, first launch (`make prod-db-up` → `make prod-restore` → `make prod-up`),
verification, updates (`git pull && make prod-up`), and rollback/snapshots are **identical to
`DEPLOY_LIGHTSAIL.md §3–§11`**. Two adjustments:
- Deploy runs `./deploy/fetch-secrets.sh` before `make prod-up` (step 4).
- `deploy/backup.sh` needs no `aws configure` — the instance role authenticates the S3 upload.

## 7. Secret rotation

- **Secrets Manager**: enable rotation on the secret (e.g. the DB password via a rotation Lambda), then
  re-run `./deploy/fetch-secrets.sh && make prod-up` to pick up new values. Nothing is pinned in git.
- **SSM**: `aws ssm put-parameter --overwrite ...` then re-fetch + `make prod-up`.
