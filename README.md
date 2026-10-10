# inventory-export-lambda

A single AWS Lambda that pulls account inventory via **Resource Explorer v2**
(`search`, the boto3 equivalent of `aws resource-explorer-2 search`), writes it
to a CSV, and uploads the CSV to an S3 bucket. CloudFormation provisions the
Lambda, its IAM role, and the bucket.

## Prerequisites

- AWS Resource Explorer must be **enabled** in the account with an aggregator
  index and a default view. Without a default view, `search` returns an error.
- AWS CLI configured with credentials that can deploy CloudFormation.

## Files

- `src/handler.py` — Lambda function code (Python 3.12).
- `template.yaml` — CloudFormation: Lambda + IAM role + S3 bucket.
- `deploy.sh` — one-step deploy script (stack + handler code). See [Deploy](#deploy).
- `README.md` — this file.

## Deploy

Use `deploy.sh`. It does the full deploy in one step: creates/updates the
CloudFormation stack (Lambda + IAM role + S3 bucket), then packages
`src/handler.py` and pushes it to the function, replacing the placeholder code.

Make sure your shell has valid AWS credentials first (`aws sts
get-caller-identity` should succeed), then run:

```sh
./deploy.sh --bucket your-unique-bucket-name
```

Bucket names are globally unique and lowercase. Omit `--bucket` to let
CloudFormation generate a name automatically.

### Options

| Flag | Description | Default |
| --- | --- | --- |
| `-b, --bucket NAME` | S3 bucket name for inventory CSVs. Omit for an auto-generated name. | (auto-generated) |
| `-s, --stack-name NAME` | CloudFormation stack name. | `inventory-export-lambda` |
| `-f, --function-name NAME` | Lambda function name. | `inventory-export-lambda` |
| `-q, --query STRING` | Resource Explorer query string. | `*` |
| `-r, --region REGION` | AWS region. | current CLI/env region |
| `-p, --profile NAME` | AWS CLI profile to use. | default credential chain |
| `--skip-code` | Deploy the stack only; keep the placeholder code. | off |
| `-h, --help` | Show usage and exit. | |

Credentials: with no `--profile`, the script uses the default AWS credential
chain (environment variables, then the `default` profile). Pass `--profile` to
use a named profile instead.

### Examples

```sh
# Simplest deploy, default credential chain
./deploy.sh --bucket valere-aws-account-inventory

# Pin a region and use a named profile
./deploy.sh -b my-inventory-bucket -r us-east-1 -p my-profile

# Deploy the stack only, without pushing handler code
./deploy.sh --bucket my-inventory-bucket --skip-code
```

On success the script prints the stack outputs (function name, bucket, role
ARN) as a table.

## Invoke

```sh
aws lambda invoke \
  --function-name inventory-export-lambda \
  --payload '{"queryString": "*"}' \
  response.json
```

The function uploads to `s3://<bucket>/inventory/<ISO-8601-timestamp>.csv` and
returns the bucket, key, and resource count.

## Configuration

Environment variables (set by CloudFormation):

- `TARGET_BUCKET` — destination S3 bucket (required).
- `RESOURCE_QUERY` — Resource Explorer query string; defaults to `*` (all).

## Notes

- `boto3` is provided by the Lambda runtime; no bundled dependencies needed.
- The IAM role grants `resource-explorer-2:Search` (account-scoped, so
  `Resource: "*"`) and `s3:PutObject` limited to the inventory bucket.
