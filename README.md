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
- `README.md` — this file.

## Deploy

The template ships with placeholder inline code so the stack is valid on its
own. Deploy the stack, then push the real code from `src/`.

1. Create the stack (bucket name auto-generated if left blank):

   ```sh
   aws cloudformation deploy \
     --template-file template.yaml \
     --stack-name inventory-export-lambda \
     --capabilities CAPABILITY_IAM
   ```

2. Package and upload the real handler code:

   ```sh
   cd src && zip ../function.zip handler.py && cd ..
   aws lambda update-function-code \
     --function-name inventory-export-lambda \
     --zip-file fileb://function.zip
   ```

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
