import json
import os
import io
import logging
import numpy as np
import boto3
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
MAIN_INDEX_NAME   = os.environ["INDEX_NAME"]


# --- Helper Functions ---

def clean_value(val):
    """Convert NaN, numpy types to JSON-serializable values."""
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        return float(val)
    return val

def generate_embeddings(text):
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

def build_embedding_text(json_data):
    embedding_text = [
        json_data.get("title") or "",
        json_data.get("main_category") or "",
        " ".join(json_data.get("features") or []),
        " ".join(json_data.get("description") or []),
    ]
    return " ".join(filter(None, embedding_text))

def add_embeddings_to_data(json_data):
    embedding_text = build_embedding_text(json_data)
    embeddings = generate_embeddings(embedding_text)
    json_data["embedding"] = embeddings
    return json_data

def process_row(row):
    json_data = {
        "parent_asin":    clean_value(row.get("parent_asin")),
        "main_category":  clean_value(row.get("main_category")),
        "title":          clean_value(row.get("title")),
        "average_rating": clean_value(row.get("average_rating")),
        "rating_number":  clean_value(row.get("rating_number")),
        "price":          clean_value(row.get("price")),
        "store":          clean_value(row.get("store")),
        "features":       row.get("features") if isinstance(row.get("features"), list) else None,
        "description":    row.get("description") if isinstance(row.get("description"), list) else clean_value(row.get("description")),
        "categories":     row.get("categories") if isinstance(row.get("categories"), list) else None,
        "details":        row.get("details") if isinstance(row.get("details"), dict) else None
    }
    json_data = add_embeddings_to_data(json_data)
    return json_data

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

def push_data_to_opensearch(client, processed_data):
    actions = []
    for document in processed_data:
        actions.append({
            "_index":  MAIN_INDEX_NAME,
            "_id":     document.get("parent_asin"),
            "_source": document
        })
    if actions:
        success, errors = bulk(client, actions, raise_on_error=False)
        logger.info(f"Indexed: {success} | Failed: {len(errors)}")
        if errors:
            logger.error(f"Bulk indexing errors: {errors}")


# --- Lambda Handler ---

def lambda_handler(event, context):
    # 1. get the json file key from the event
    json_file = event["json_file"]
    logger.info(f"Loading s3://{S3_BUCKET}/{json_file}")

    # 2. load the file from s3
    response = s3.get_object(Bucket=S3_BUCKET, Key=json_file)
    file_content = response["Body"].read().decode("utf-8")

    # handles both json lines (.jsonl) and json array formats
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

    # 3. process each row
    processed_data = []
    failed = 0
    for _, row in df.iterrows():
        try:
            json_data_row = process_row(row.to_dict())
            processed_data.append(json_data_row)
        except Exception as e:
            failed += 1
            logger.error(f"Failed to process row {row.get('parent_asin', 'unknown')}: {e}")
            continue

    logger.info(f"Processing complete — succeeded: {len(processed_data)} | failed: {failed}")

    # 4. push to opensearch
    logger.info(f"Pushing {len(processed_data)} documents to OpenSearch index '{MAIN_INDEX_NAME}'")
    opensearch_client = get_opensearch_client(OPENSEARCH_DOMAIN)
    push_data_to_opensearch(opensearch_client, processed_data)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "processed": len(processed_data),
            "failed":    failed,
            "source":    json_file
        })
    }