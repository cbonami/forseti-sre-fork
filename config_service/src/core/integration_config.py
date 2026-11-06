"""
Integration Configuration System

Manages integrations with different ownership levels:
- Org-level: Shared credentials (OpenAI API keys)
- Team-level: Team-specific settings (Grafana URL, API key)

Config Structure:
{
    "integrations": {
        "openai": {
            "level": "org",
            "locked": true,
            "config_schema": {...},
            "api_key": "sk-..."
        },
        "grafana": {
            "level": "team",
            "config_schema": {...},
            "domain": "https://grafana.team.com",
            "api_key": "glsa_..."
        }
    }
}

Note: Config values are stored at the top level of each integration object,
alongside metadata keys (level, locked, config_schema, team_config_schema).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class IntegrationLevel(str, Enum):
    """Level at which integration is configured."""

    ORG = "org"
    TEAM = "team"


@dataclass
class IntegrationFieldSchema:
    """Schema for an integration field."""

    name: str
    type: str  # string, secret, boolean, integer
    required: bool = False
    default: Any = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    placeholder: Optional[str] = None
    allowed_values: Optional[List[Any]] = None


@dataclass
class IntegrationSchema:
    """Schema for an integration."""

    id: str
    name: str
    description: str
    level: IntegrationLevel
    locked: bool = False
    required: bool = False

    # Fields configured at org level
    org_fields: List[IntegrationFieldSchema] = field(default_factory=list)

    # Fields configured at team level
    team_fields: List[IntegrationFieldSchema] = field(default_factory=list)

    # Documentation
    docs_url: Optional[str] = None
    setup_instructions: Optional[str] = None


# =============================================================================
# Integration Schemas
# =============================================================================

INTEGRATION_SCHEMAS: Dict[str, IntegrationSchema] = {
    "openai": IntegrationSchema(
        id="openai",
        name="OpenAI",
        description="OpenAI API for LLM access",
        level=IntegrationLevel.ORG,
        locked=True,
        required=True,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="OpenAI API key",
            ),
            IntegrationFieldSchema(
                name="org_id",
                type="string",
                required=False,
                display_name="Organization ID",
                description="OpenAI organization ID (optional)",
            ),
        ],
        team_fields=[],
        docs_url="https://platform.openai.com/docs",
    ),
    "github": IntegrationSchema(
        id="github",
        name="GitHub",
        description="GitHub integration for code and PR access",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="Access Token",
                description="Personal access token or GitHub App token",
            ),
            IntegrationFieldSchema(
                name="default_org",
                type="string",
                required=False,
                display_name="Organization",
                description="Default GitHub organization",
            ),
        ],
        team_fields=[
            IntegrationFieldSchema(
                name="default_repo",
                type="string",
                required=False,
                display_name="Default Repository",
                description="Default repo for this team",
                placeholder="owner/repo",
            ),
        ],
    ),
    "kubernetes": IntegrationSchema(
        id="kubernetes",
        name="Kubernetes",
        description="Kubernetes cluster access",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="domain",
                type="string",
                required=True,
                display_name="Kubernetes API URL",
                description="Kubernetes API server URL",
                placeholder="https://k8s.example.com:6443",
            ),
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Token",
                description="Kubernetes service account bearer token",
            ),
        ],
        team_fields=[
            IntegrationFieldSchema(
                name="namespace",
                type="string",
                required=False,
                default="default",
                display_name="Default Namespace",
            ),
            IntegrationFieldSchema(
                name="allowed_namespaces",
                type="string",
                required=False,
                display_name="Allowed Namespaces",
                description="Comma-separated list of namespaces team can access",
            ),
        ],
    ),
    "grafana": IntegrationSchema(
        id="grafana",
        name="Grafana",
        description="Grafana for dashboards and Prometheus queries",
        level=IntegrationLevel.TEAM,
        locked=False,
        required=False,
        org_fields=[],
        team_fields=[
            IntegrationFieldSchema(
                name="domain",
                type="string",
                required=True,
                display_name="Grafana URL",
                description="Your Grafana instance URL",
                placeholder="https://grafana.example.com",
            ),
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Service account token or API key",
            ),
            IntegrationFieldSchema(
                name="default_datasource",
                type="string",
                required=False,
                default="prometheus",
                display_name="Default Datasource",
            ),
        ],
    ),
    "pagerduty": IntegrationSchema(
        id="pagerduty",
        name="PagerDuty",
        description="PagerDuty for incident management",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
            ),
        ],
        team_fields=[
            IntegrationFieldSchema(
                name="service_id",
                type="string",
                required=False,
                display_name="Service ID",
                description="PagerDuty service for this team",
            ),
            IntegrationFieldSchema(
                name="escalation_policy_id",
                type="string",
                required=False,
                display_name="Escalation Policy",
            ),
        ],
    ),
    "tavily": IntegrationSchema(
        id="tavily",
        name="Tavily",
        description="Tavily Search API for web search capabilities",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Tavily API key",
                placeholder="tvly-...",
            ),
        ],
        team_fields=[],
        docs_url="https://tavily.com/",
        setup_instructions="1. Sign up at https://tavily.com/\n2. Get your API key from the dashboard\n3. Add it to the integration settings",
    ),
    "llm": IntegrationSchema(
        id="llm",
        name="LLM Model",
        description="Preferred LLM model for the AI agent (e.g., openai/gpt-4o, gemini/gemini-2.5-flash)",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="model",
                type="string",
                required=True,
                display_name="Model ID",
                description="LiteLLM-compatible model ID",
                placeholder="openrouter/openai/gpt-4o",
            ),
        ],
        team_fields=[],
    ),
    "gemini": IntegrationSchema(
        id="gemini",
        name="Google Gemini",
        description="Google Gemini API for LLM access",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Google AI / Gemini API key",
                placeholder="AIza...",
            ),
        ],
        team_fields=[],
        docs_url="https://ai.google.dev/docs",
    ),
    "deepseek": IntegrationSchema(
        id="deepseek",
        name="DeepSeek",
        description="DeepSeek API for LLM access",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="DeepSeek API key",
                placeholder="sk-...",
            ),
        ],
        team_fields=[],
        docs_url="https://platform.deepseek.com/docs",
    ),
    "openrouter": IntegrationSchema(
        id="openrouter",
        name="OpenRouter",
        description="OpenRouter unified API — access 200+ models from one API key",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="OpenRouter API key",
                placeholder="sk-or-v1-...",
            ),
        ],
        team_fields=[],
        docs_url="https://openrouter.ai/docs",
    ),
    "azure": IntegrationSchema(
        id="azure",
        name="Azure OpenAI",
        description="Azure-hosted OpenAI models",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Azure OpenAI API key",
            ),
            IntegrationFieldSchema(
                name="api_base",
                type="string",
                required=True,
                display_name="Endpoint URL",
                description="Azure OpenAI resource endpoint",
                placeholder="https://your-resource.openai.azure.com",
            ),
            IntegrationFieldSchema(
                name="api_version",
                type="string",
                required=False,
                default="2024-06-01",
                display_name="API Version",
                description="Azure API version",
            ),
        ],
        team_fields=[],
    ),
    "bedrock": IntegrationSchema(
        id="bedrock",
        name="Amazon Bedrock",
        description="AWS Bedrock for managed LLM inference",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=False,
                display_name="Bedrock API Key",
                description="Bedrock API key (ABSK...) — simplest option",
                placeholder="ABSK...",
            ),
            IntegrationFieldSchema(
                name="aws_access_key_id",
                type="secret",
                required=False,
                display_name="AWS Access Key ID",
                description="IAM access key (alternative to Bedrock API key)",
            ),
            IntegrationFieldSchema(
                name="aws_secret_access_key",
                type="secret",
                required=False,
                display_name="AWS Secret Access Key",
                description="IAM secret key (required with access key)",
            ),
            IntegrationFieldSchema(
                name="aws_region_name",
                type="string",
                required=False,
                default="us-east-1",
                display_name="AWS Region",
            ),
        ],
        team_fields=[],
        docs_url="https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html",
        setup_instructions=(
            "Option A (recommended): Generate a Bedrock API key in the AWS Console → Amazon Bedrock → API keys\n"
            "Option B: Create an IAM user with bedrock:InvokeModel permission and provide access keys"
        ),
    ),
    "vertex_ai": IntegrationSchema(
        id="vertex_ai",
        name="Google Vertex AI",
        description="Google Cloud Vertex AI for managed model inference (Gemini, Claude, Llama, etc.)",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="project",
                type="string",
                required=True,
                display_name="GCP Project ID",
                description="Google Cloud project ID",
                placeholder="my-gcp-project",
            ),
            IntegrationFieldSchema(
                name="location",
                type="string",
                required=False,
                default="us-central1",
                display_name="Region",
                description="GCP region for Vertex AI",
                placeholder="us-central1",
            ),
            IntegrationFieldSchema(
                name="service_account_json",
                type="secret",
                required=False,
                display_name="Service Account JSON",
                description="GCP service account key JSON (optional if using workload identity)",
            ),
        ],
        team_fields=[],
        docs_url="https://cloud.google.com/vertex-ai/docs",
    ),
    "mistral": IntegrationSchema(
        id="mistral",
        name="Mistral AI",
        description="Mistral AI API for LLM access",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Mistral API key",
            ),
        ],
        team_fields=[],
        docs_url="https://docs.mistral.ai/",
    ),
    "cohere": IntegrationSchema(
        id="cohere",
        name="Cohere",
        description="Cohere API for LLM and embeddings",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Cohere API key",
            ),
        ],
        team_fields=[],
        docs_url="https://docs.cohere.com/",
    ),
    "together_ai": IntegrationSchema(
        id="together_ai",
        name="Together AI",
        description="Together AI for open-source model inference",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Together AI API key",
            ),
        ],
        team_fields=[],
        docs_url="https://docs.together.ai/",
    ),
    "groq": IntegrationSchema(
        id="groq",
        name="Groq",
        description="Groq ultra-fast LLM inference",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Groq API key",
            ),
        ],
        team_fields=[],
        docs_url="https://console.groq.com/docs",
    ),
    "fireworks_ai": IntegrationSchema(
        id="fireworks_ai",
        name="Fireworks AI",
        description="Fireworks AI for fast model inference",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Fireworks AI API key",
            ),
        ],
        team_fields=[],
        docs_url="https://docs.fireworks.ai/",
    ),
    "xai": IntegrationSchema(
        id="xai",
        name="xAI (Grok)",
        description="xAI API for Grok models",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="xAI API Key",
                description="API key from console.x.ai",
                placeholder="xai-...",
            ),
        ],
        team_fields=[],
        docs_url="https://docs.x.ai/",
    ),
    "moonshot": IntegrationSchema(
        id="moonshot",
        name="Moonshot AI (Kimi)",
        description="Moonshot AI API for Kimi models",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="Moonshot API Key",
                description="API key from platform.moonshot.cn",
                placeholder="sk-...",
            ),
        ],
        team_fields=[],
        docs_url="https://platform.moonshot.cn/docs",
    ),
    "minimax": IntegrationSchema(
        id="minimax",
        name="MiniMax",
        description="MiniMax API for MiniMax-Text models",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="MiniMax API Key",
                description="API key from api.minimax.chat",
                placeholder="sk-api-...",
            ),
        ],
        team_fields=[],
        docs_url="https://www.minimax.chat/",
    ),
    "azure_ai": IntegrationSchema(
        id="azure_ai",
        name="Azure AI Foundry",
        description="Azure AI Foundry serverless model deployments",
        level=IntegrationLevel.ORG,
        locked=False,
        required=False,
        org_fields=[
            IntegrationFieldSchema(
                name="api_key",
                type="secret",
                required=True,
                display_name="API Key",
                description="Azure AI Foundry deployment API key",
            ),
            IntegrationFieldSchema(
                name="api_base",
                type="string",
                required=True,
                display_name="Endpoint URL",
                description="Serverless model deployment endpoint URL",
                placeholder="https://your-model.eastus2.models.ai.azure.com",
            ),
        ],
        team_fields=[],
        docs_url="https://learn.microsoft.com/en-us/azure/ai-foundry/",
    ),
    "ollama": IntegrationSchema(
        id="ollama",
        name="Ollama",
        description="Local LLM inference via Ollama",
        level=IntegrationLevel.TEAM,
        locked=False,
        required=False,
        org_fields=[],
        team_fields=[
            IntegrationFieldSchema(
                name="host",
                type="string",
                required=True,
                default="http://localhost:11434",
                display_name="Ollama Host",
                description="Ollama server URL",
                placeholder="http://localhost:11434",
            ),
        ],
    ),
}


def get_integration_schema(integration_id: str) -> Optional[IntegrationSchema]:
    """Get schema for an integration."""
    return INTEGRATION_SCHEMAS.get(integration_id)


def get_all_integration_schemas() -> List[IntegrationSchema]:
    """Get all integration schemas."""
    return list(INTEGRATION_SCHEMAS.values())


# =============================================================================
# Integration Config Resolution
# =============================================================================


@dataclass
class ResolvedIntegration:
    """Resolved integration configuration with merged values."""

    id: str
    name: str
    level: IntegrationLevel
    enabled: bool
    configured: bool  # All required fields present

    # Merged config values
    config: Dict[str, Any]

    # What's missing
    missing_fields: List[str]


def resolve_integration(
    integration_id: str,
    org_config: Dict[str, Any],
    team_config: Optional[Dict[str, Any]] = None,
) -> ResolvedIntegration:
    """
    Resolve an integration's configuration.

    Args:
        integration_id: Integration identifier
        org_config: Organization-level config for this integration
        team_config: Team-level config for this integration

    Returns:
        ResolvedIntegration with merged values
    """
    schema = get_integration_schema(integration_id)
    if not schema:
        return ResolvedIntegration(
            id=integration_id,
            name=integration_id,
            level=IntegrationLevel.ORG,
            enabled=False,
            configured=False,
            config={},
            missing_fields=[],
        )

    # Start with defaults
    config = {}
    missing = []

    # Apply org fields
    for field in schema.org_fields:
        value = org_config.get(field.name)
        if value is not None:
            config[field.name] = value
        elif field.default is not None:
            config[field.name] = field.default
        elif field.required:
            missing.append(field.name)

    # Apply team fields
    team_cfg = team_config or {}
    for field in schema.team_fields:
        value = team_cfg.get(field.name)
        if value is not None:
            config[field.name] = value
        elif field.default is not None:
            config[field.name] = field.default
        elif field.required:
            missing.append(field.name)

    # Check if enabled
    enabled = org_config.get("enabled", True) and team_cfg.get("enabled", True)

    return ResolvedIntegration(
        id=integration_id,
        name=schema.name,
        level=schema.level,
        enabled=enabled,
        configured=len(missing) == 0,
        config=config,
        missing_fields=missing,
    )


def resolve_all_integrations(
    effective_config: Dict[str, Any],
) -> Dict[str, ResolvedIntegration]:
    """
    Resolve all integrations from effective config.

    Args:
        effective_config: The merged effective configuration

    Returns:
        Dict of integration_id → ResolvedIntegration
    """
    integrations_config = effective_config.get("integrations", {})
    result = {}

    for integration_id, schema in INTEGRATION_SCHEMAS.items():
        int_config = integrations_config.get(integration_id, {})
        org_config = int_config.get("config", {})
        team_config = int_config.get("team_config", {})

        result[integration_id] = resolve_integration(
            integration_id,
            org_config,
            team_config,
        )

    return result


def get_missing_required_integrations(
    effective_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Get list of required integrations that are not fully configured.

    Returns:
        List of {id, name, missing_fields}
    """
    resolved = resolve_all_integrations(effective_config)
    missing = []

    for integration_id, integration in resolved.items():
        schema = get_integration_schema(integration_id)
        if schema and schema.required and not integration.configured:
            missing.append(
                {
                    "id": integration_id,
                    "name": integration.name,
                    "missing_fields": integration.missing_fields,
                }
            )

    return missing


def get_integration_config_for_tool(
    tool_name: str,
    effective_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Get integration configuration for a specific tool.

    Maps tool names to their integration configs.
    """
    tool_to_integration = {
        "grafana_query_prometheus": "grafana",
        "grafana_list_dashboards": "grafana",
        "grafana_get_dashboard": "grafana",
        "search_github_code": "github",
        "read_github_file": "github",
        "web_search": "tavily",
    }

    integration_id = tool_to_integration.get(tool_name)
    if not integration_id:
        return {}

    resolved = resolve_all_integrations(effective_config)
    integration = resolved.get(integration_id)

    if integration and integration.configured:
        return integration.config

    return {}
