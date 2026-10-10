"""AWS Lambda that pulls account inventory via Resource Explorer v2,
writes it to a CSV file, and uploads the CSV to an S3 bucket.

boto3 equivalent of:  aws resource-explorer-2 search --query-string <query>
"""

import csv
import io
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Retry/timeout config applied to all AWS calls (Requirement 1.1/1.5, 3.1).
_BOTO_CONFIG = Config(
    connect_timeout=30,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
)

# Guard against runaway pagination (Requirement 1.2).
MAX_PAGES = 10_000

# Default query returns all resources the view can see.
DEFAULT_QUERY = os.environ.get("RESOURCE_QUERY", "")

# Columns emitted for every row. "Properties" is JSON-stringified.
BASE_FIELDS = [
    "Arn",
    "OwningAccountId",
    "Region",
    "Service",
    "ResourceType",
    "LastReportedAt",
    "Properties",
]


def _get_bucket_name():
    """Read the target bucket from configuration (Requirement 3.2, 3.6)."""
    bucket = os.environ.get("TARGET_BUCKET")
    if not bucket:
        raise ValueError(
            "TARGET_BUCKET environment variable is not set; cannot upload CSV."
        )
    return bucket


def discover_regions(ec2_client):
    """Return all regions enabled for this account via EC2 describe_regions.

    Includes opt-in regions that have been enabled; excludes those that are
    not opted into, since Resource Explorer cannot be queried there anyway.
    """
    response = ec2_client.describe_regions(AllRegions=False)
    regions = [r["RegionName"] for r in response.get("Regions", [])]
    regions.sort()
    logger.info("Discovered %d enabled regions: %s", len(regions), ", ".join(regions))
    return regions


def search_region(query_string, region):
    """Run a paginated Resource Explorer search against a single region.

    Returns a (possibly empty) list of resources. Regions where Resource
    Explorer has no index/view raise, which the caller handles so one bad
    region does not abort the whole run.
    """
    client = boto3.client("resource-explorer-2", region_name=region, config=_BOTO_CONFIG)
    return search_all_resources(client, query_string)


def search_all_regions(ec2_client, query_string):
    """Iterate every enabled region, search each, and merge de-duplicated results.

    De-duplication is keyed on ARN so that an aggregator index returning
    cross-region hits does not produce duplicate rows.
    """
    merged = {}
    for region in discover_regions(ec2_client):
        try:
            region_resources = search_region(query_string, region)
        except Exception as exc:  # noqa: BLE001 - log and continue per region
            logger.warning(
                "Skipping region %s; Resource Explorer search failed: %s",
                region,
                exc,
            )
            continue

        new_count = 0
        for resource in region_resources:
            arn = resource.get("Arn")
            # Fall back to identity key when ARN missing, to avoid dropping rows.
            key = arn or id(resource)
            if key not in merged:
                merged[key] = resource
                new_count += 1
        logger.info(
            "Region %s: %d resources (%d new after de-dup).",
            region,
            len(region_resources),
            new_count,
        )

    return list(merged.values())


def search_all_resources(client, query_string):
    """Paginate through Resource Explorer search results following NextToken.

    boto3 equivalent of `aws resource-explorer-2 search`, accumulating every
    resource across all pages (Requirement 1).
    """
    resources = []
    next_token = None
    pages = 0

    while True:
        kwargs = {}
        # QueryString is required by the API; empty string is not accepted,
        # so only pass it when provided, otherwise use a match-all wildcard.
        kwargs["QueryString"] = query_string if query_string else "*"
        if next_token:
            kwargs["NextToken"] = next_token

        response = client.search(**kwargs)
        page_resources = response.get("Resources", [])
        resources.extend(page_resources)
        pages += 1

        next_token = response.get("NextToken")
        logger.info(
            "Fetched page %d (%d resources, %d total so far)",
            pages,
            len(page_resources),
            len(resources),
        )

        if not next_token:
            break
        if pages >= MAX_PAGES:
            logger.warning("Reached MAX_PAGES (%d); stopping pagination.", MAX_PAGES)
            break

    return resources


def _flatten(resource):
    """Flatten a Resource Explorer resource into a flat dict for CSV."""
    last_reported = resource.get("LastReportedAt")
    if isinstance(last_reported, datetime):
        last_reported = last_reported.isoformat()

    # Properties is a list of {Name, Data, LastReportedAt}; serialize compactly.
    props = resource.get("Properties", [])

    return {
        "Arn": resource.get("Arn", ""),
        "OwningAccountId": resource.get("OwningAccountId", ""),
        "Region": resource.get("Region", ""),
        "Service": resource.get("Service", ""),
        "ResourceType": resource.get("ResourceType", ""),
        "LastReportedAt": last_reported or "",
        "Properties": _properties_to_str(props),
    }


def _properties_to_str(props):
    import json

    try:
        return json.dumps(props, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(props)


def build_csv(resources):
    """Serialize resources to a CSV string (Requirement 2).

    Uses the csv module, which handles RFC 4180 escaping automatically.
    Always writes a header row, even when there are no resources.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=BASE_FIELDS,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for resource in resources:
        writer.writerow(_flatten(resource))
    return buffer.getvalue()


def _object_key():
    """Unique, timestamped S3 key with millisecond precision (Requirement 3.3)."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S-") + f"{now.microsecond // 1000:03d}Z"
    return f"inventory/{stamp}.csv"


def upload_csv(s3_client, bucket, key, csv_body):
    """Upload the CSV to S3 (Requirement 3)."""
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=csv_body.encode("utf-8"),
        ContentType="text/csv",
    )


def lambda_handler(event, context):
    """Entry point. Returns the bucket and key on success (Requirement 3.4)."""
    bucket = _get_bucket_name()

    query_string = (event or {}).get("queryString", DEFAULT_QUERY)

    ec2_client = boto3.client("ec2", config=_BOTO_CONFIG)
    s3_client = boto3.client("s3", config=_BOTO_CONFIG)

    # Iterate every enabled region, searching each region's Resource Explorer
    # endpoint and merging de-duplicated results (keyed on ARN).
    resources = search_all_regions(ec2_client, query_string)
    logger.info("Retrieved %d total resources across all regions.", len(resources))

    csv_body = build_csv(resources)

    key = _object_key()
    upload_csv(s3_client, bucket, key, csv_body)
    logger.info("Uploaded CSV to s3://%s/%s", bucket, key)

    return {
        "statusCode": 200,
        "bucket": bucket,
        "key": key,
        "resourceCount": len(resources),
    }
