"""
AgentCore Gateway tool definitions (spec section 25).

Gateway exposes these as MCP tools (tools/list, tools/call). Each tool
schema below maps 1:1 to a Lambda function under lambda/ - the schemas
themselves live here so they can be registered with the Gateway via
CreateGatewayTarget without hunting through the CloudFormation template.

These are plain JSON-Schema tool definitions; Gateway config
(infrastructure/template.yaml) references this file's TOOLS list when
creating the GatewayTarget resource, or you can paste individual entries
into the AWS Console / CLI (`aws bedrock-agentcore-control
create-gateway-target`) when wiring this up manually.
"""

TOOLS = [
    {
        "name": "search_products",
        "description": "Find candidate products for a product type and Amazon marketplace country.",
        "lambda_handler": "lambda/product_search/handler.py",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_type": {"type": "string", "description": "e.g. 'wireless headphones'"},
                "country": {"type": "string", "description": "Marketplace country code, e.g. 'US'"},
                "max_results": {"type": "integer", "description": "Max candidates to return", "default": 5},
            },
            "required": ["product_type", "country"],
        },
    },
    {
        "name": "get_product",
        "description": "Get current details (title, price, rating) for a single ASIN.",
        "lambda_handler": "lambda/product_search/handler.py",
        "input_schema": {
            "type": "object",
            "properties": {
                "asin": {"type": "string"},
                "country": {"type": "string"},
            },
            "required": ["asin", "country"],
        },
    },
    {
        "name": "get_reviews",
        "description": "Fetch normalized reviews for a single ASIN (unfiltered - callers should apply date/sampling logic).",
        "lambda_handler": "lambda/review_fetch/handler.py",
        "input_schema": {
            "type": "object",
            "properties": {
                "asin": {"type": "string"},
                "country": {"type": "string"},
            },
            "required": ["asin", "country"],
        },
    },
    {
        "name": "get_price_history",
        "description": "Fetch price observations over time for a single ASIN.",
        "lambda_handler": "lambda/price_fetch/handler.py",
        "input_schema": {
            "type": "object",
            "properties": {
                "asin": {"type": "string"},
                "country": {"type": "string"},
            },
            "required": ["asin", "country"],
        },
    },
    {
        "name": "calculate_review_statistics",
        "description": "Deterministically compute rating distribution and positive/negative percentages for a set of reviews.",
        "lambda_handler": None,  # implemented in-process via product_intelligence.analysis.reviews
        "input_schema": {
            "type": "object",
            "properties": {
                "reviews": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["reviews"],
        },
    },
]


def get_tool(name: str) -> dict | None:
    return next((t for t in TOOLS if t["name"] == name), None)
