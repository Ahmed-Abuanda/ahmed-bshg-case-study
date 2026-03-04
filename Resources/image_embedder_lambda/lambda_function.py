import base64
import json
import logging
import os

import boto3
import httpx
import numpy as np
import pandas as pd
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from opensearchpy.helpers import bulk

# --- Logging ---
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --- Clients ---
s3 = boto3.client("s3")
bedrock = boto3.client(
    service_name="bedrock-runtime",
    region_name="eu-central-1"
)

# --- Environment Variables ---
S3_BUCKET         = os.environ["S3_BUCKET"]
EMBEDDING_MODEL   = os.environ["EMBEDDING_MODEL"]
OPENSEARCH_DOMAIN = os.environ["OPENSEARCH_DOMAIN"]
IMAGE_INDEX_NAME  = os.environ["IMAGE_INDEX_NAME"]
IMAGE_MODEL_ID    = os.environ["IMAGE_MODEL_ID"]


# --- Helper Functions ---

def generate_embeddings(text):
    """Generate embeddings for a given text using Bedrock."""
    if not text or not text.strip():
        return None
    body = json.dumps({
        "inputText": text,
        "dimensions": 1024,
        "normalize": True
    })
    response = bedrock.invoke_model(
        body=body,
        modelId=EMBEDDING_MODEL,
        accept="application/json",
        contentType="application/json"
    )
    response_body = json.loads(response["body"].read())
    return response_body["embedding"]


def describe_image(image_url):
    """Fetch an image from a URL and ask Nova Lite to describe it."""
    image_bytes = httpx.get(image_url, timeout=15).content
    image_data = base64.b64encode(image_bytes).decode("utf-8")

    body = json.dumps({
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": "jpeg",
                            "source": {
                                "bytes": image_data
                            }
                        }
                    },
                    {
                        "text": (
                            "Describe this product image in detail. "
                            "Focus on what the product looks like, its colors, "
                            "materials, and any notable visual features."
                        )
                    }
                ]
            }
        ]
    })

    response = bedrock.invoke_model(
        body=body,
        modelId=IMAGE_MODEL_ID,
        accept="application/json",
        contentType="application/json"
    )
    response_body = json.loads(response["body"].read())
    return response_body["output"]["message"]["content"][0]["text"]


def build_image_document(parent_asin, image_data):

    image_url = image_data.get("large")
    if not image_url:
        logger.warning(f"[{parent_asin}] Skipping image — no 'large' URL in: {image_data}")
        return None

    variant = image_data.get("variant", "UNKNOWN")
    description = describe_image(image_url)
    embedding = generate_embeddings(description)

    return {
        "doc_id":      f"{parent_asin}_{variant}",
        "parent_asin": parent_asin,
        "variant":     variant,
        "description": description,
        "embedding":   embedding,
    }


def get_opensearch_client(opensearch_url):
    credentials = boto3.Session().get_credentials()
    region = "eu-central-1"
    auth = AWSV4SignerAuth(credentials, region, "es")
    return OpenSearch(
        hosts=[{"host": opensearch_url, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=20
    )


def push_documents_to_opensearch(client, documents):
    actions = [
        {
            "_index":  IMAGE_INDEX_NAME,
            "_id":     doc["doc_id"],
            "_source": doc,
        }
        for doc in documents
    ]
    if not actions:
        logger.info("No documents to index.")
        return

    success, errors = bulk(client, actions, raise_on_error=False)
    logger.info(f"Indexed: {success} | Failed: {len(errors)}")
    if errors:
        logger.error(f"Bulk indexing errors: {errors}")


# --- Lambda Handler ---

def lambda_handler(event, context):
    json_file = event["json_file"]
    logger.info(f"Loading s3://{S3_BUCKET}/{json_file}")

    response = s3.get_object(Bucket=S3_BUCKET, Key=json_file)
    file_content = response["Body"].read().decode("utf-16")

    try:
        data = json.loads(file_content)
        if isinstance(data, dict):
            data = [data]
        df = pd.DataFrame(data)
    except json.JSONDecodeError:
        logger.warning("Failed to parse as JSON array, attempting JSONL format")
        records = [json.loads(line) for line in file_content.strip().splitlines() if line.strip()]
        df = pd.DataFrame(records)

    logger.info(f"Loaded {len(df)} records from {json_file}")

    rows_with_images = df[df["images"].notna()]
    logger.info(f"Rows with images: {len(rows_with_images)}")

    documents = []
    failed = 0

    for _, row in rows_with_images.iterrows():
        parent_asin = row.get("parent_asin", "unknown")
        images = row["images"]

        if not isinstance(images, list):
            logger.warning(f"[{parent_asin}] 'images' is not a list, skipping.")
            continue

        for image_data in images:
            if not isinstance(image_data, dict):
                continue
            try:
                doc = build_image_document(parent_asin, image_data)
                if doc:
                    documents.append(doc)
            except Exception as e:
                failed += 1
                logger.error(
                    f"[{parent_asin}] Failed to process image "
                    f"{image_data.get('large', 'N/A')}: {e}"
                )

    logger.info(f"Processing complete — succeeded: {len(documents)} | failed: {failed}")

    logger.info(f"Pushing {len(documents)} image documents to index '{IMAGE_INDEX_NAME}'")
    opensearch_client = get_opensearch_client(OPENSEARCH_DOMAIN)
    push_documents_to_opensearch(opensearch_client, documents)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "processed": len(documents),
            "failed":    failed,
            "source":    json_file
        })
    }