import json
import os
import logging
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

# --- Logging ---
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --- Environment Variables ---
OPENSEARCH_DOMAIN = os.environ["OPENSEARCH_DOMAIN"]
MAIN_INDEX_NAME = os.environ["MAIN_INDEX_NAME"]
IMAGE_INDEX_NAME = os.environ["IMAGE_INDEX_NAME"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "amazon.nova-lite-v1:0")

# --- Clients ---
bedrock = boto3.client(
    service_name="bedrock-runtime",
    region_name=os.environ.get("AWS_REGION", "eu-central-1")
)

_opensearch_client = None


def get_opensearch_client():
    """Build OpenSearch client with IAM auth."""
    global _opensearch_client
    if _opensearch_client is None:
        credentials = boto3.Session().get_credentials()
        region = os.environ.get("AWS_REGION", "eu-central-1")
        auth = AWSV4SignerAuth(credentials, region, "es")
        _opensearch_client = OpenSearch(
            hosts=[{"host": OPENSEARCH_DOMAIN.replace("https://", "").replace("http://", ""), "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20,
            timeout=300,
            max_retries=3,
            retry_on_timeout=True
        )
    return _opensearch_client


def generate_embeddings(text):
    """Generate embedding vector for text using Bedrock Titan."""
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


def query_text_index(question, top_k=5):
    """Search the main product index using vector similarity."""
    embedding = generate_embeddings(question)
    if embedding is None:
        return "No valid query."

    opensearch_client = get_opensearch_client()
    response = opensearch_client.search(
        index=MAIN_INDEX_NAME,
        body={
            "size": top_k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": embedding,
                        "k": top_k
                    }
                }
            },
            "_source": ["parent_asin", "title", "main_category", "price", "average_rating", "features", "description"]
        }
    )

    hits = response["hits"]["hits"]
    if not hits:
        return "No products found."

    results = []
    for hit in hits:
        src = hit["_source"]
        results.append(
            f"ASIN: {src.get('parent_asin')} | {src.get('title')} | "
            f"${src.get('price', 'N/A')} | Rating: {src.get('average_rating')} | "
            f"Features: {', '.join(src.get('features') or [])} | "
            f"Description: {src.get('description')}"
        )
    return "\n".join(results)


def query_image_index(question, top_k=5):
    """Search the image index using vector similarity."""
    embedding = generate_embeddings(question)
    if embedding is None:
        return "No valid query."

    opensearch_client = get_opensearch_client()
    response = opensearch_client.search(
        index=IMAGE_INDEX_NAME,
        body={
            "size": top_k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": embedding,
                        "k": top_k
                    }
                }
            },
            "_source": ["parent_asin", "variant", "description"]
        }
    )

    hits = response["hits"]["hits"]
    if not hits:
        return "No images found."

    results = []
    for hit in hits:
        src = hit["_source"]
        results.append(
            f"ASIN: {src.get('parent_asin')} | Variant: {src.get('variant')} | "
            f"Image: {src.get('description')}"
        )
    return "\n".join(results)


tools = [
    {
        "name": "query_text_index",
        "description": "Search the product catalogue using a question. Returns matching products with titles, prices, ratings and features.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The search query or question about products"
                }
            },
            "required": ["question"]
        }
    },
    {
        "name": "query_image_index",
        "description": "Search product images using a question. Returns matching images with visual descriptions. Use this when the question is about appearance, colour, design or visuals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The search query about product appearance or visuals"
                }
            },
            "required": ["question"]
        }
    }
]


def run_tool(tool_name, tool_input):
    if tool_name == "query_text_index":
        return query_text_index(tool_input["question"])
    elif tool_name == "query_image_index":
        return query_image_index(tool_input["question"])
    else:
        return f"Unknown tool: {tool_name}"


def send_message(user_message):
    """Send a message to the agent and return its final response."""
    logger.info("User: %s", user_message)

    messages = [{"role": "user", "content": [{"text": user_message}]}]

    while True:
        response = bedrock.invoke_model(
            modelId=AGENT_MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "schemaVersion": "messages-v1",
                "system": [{
                    "text": (
                        "You are a helpful product shopping assistant. "
                        "You have access to tools that search a product catalogue. "
                        "IMPORTANT: You must ONLY use information returned by the tools to answer questions. "
                        "Do NOT use your own knowledge to describe, recommend, or invent product details. "
                        "If the tools return no relevant results, say so honestly do not guess or fabricate any information "
                        "You may call tools multiple times to gather all necessary information. "
                        "Always cite the ASIN when referencing a specific product. "
                        "When you have enough information, respond with ONLY the final answer no preamble, no reasoning, no tool call summaries. Be concise and direct."
                    )
                }],
                "messages": messages,
                "toolConfig": {
                    "tools": [
                        {
                            "toolSpec": {
                                "name": tool["name"],
                                "description": tool["description"],
                                "inputSchema": {"json": tool["input_schema"]}
                            }
                        }
                        for tool in tools
                    ]
                },
                "inferenceConfig": {
                    "maxTokens": 1024
                }
            })
        )

        response_body = json.loads(response["body"].read())
        output_message = response_body["output"]["message"]
        stop_reason = response_body["stopReason"]
        content = output_message["content"]

        # add assistant response to message history
        messages.append({"role": "assistant", "content": content})

        # if nova is done, return the final text response
        if stop_reason == "end_turn":
            final_text = next((b["text"] for b in content if "text" in b), "")
            logger.info("Agent: %s", final_text)
            return final_text

        # if nova wants to use a tool, run it and feed results back
        if stop_reason == "tool_use":
            tool_results = []
            for block in content:
                if "toolUse" in block:
                    tool_use = block["toolUse"]
                    logger.info("Calling tool: %s with input: %s", tool_use["name"], tool_use["input"])
                    result = run_tool(tool_use["name"], tool_use["input"])
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use["toolUseId"],
                            "content": [{"text": result}]
                        }
                    })

            messages.append({"role": "user", "content": tool_results})


def lambda_handler(event, context):
    try:
        if isinstance(event.get("body"), str):
            body = json.loads(event["body"])
        else:
            body = event or {}

        question = body.get("question")
        if not question:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing required field: question"})
            }

        result = send_message(question)
        return {
            "statusCode": 200,
            "body": json.dumps({"response": result})
        }
    except Exception as e:
        logger.exception("Invoke agent failed")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
