#!/usr/bin/env python3
"""Add missing integration schemas to the database.

This script adds schemas for integrations that have tools defined but no schema:
- Jira
- Microsoft Teams
- Confluence
- Loki
- Jaeger
- Prometheus
"""

import os
import sys

from sqlalchemy import create_engine, text

# Database connection from environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable not set")
    sys.exit(1)

# All missing integration schemas
INTEGRATION_SCHEMAS = [
    {
        "id": "jira",
        "name": "Jira",
        "category": "project-management",
        "description": "Atlassian Jira for issue tracking and incident tickets",
        "docs_url": "https://developer.atlassian.com/cloud/jira/platform/rest/v3/",
        "display_order": 50,
        "featured": False,
        "fields": [
            {
                "name": "domain",
                "type": "string",
                "required": True,
                "level": "org",
                "description": "Jira instance URL",
                "placeholder": "https://company.atlassian.net",
            },
            {
                "name": "email",
                "type": "string",
                "required": True,
                "level": "org",
                "description": "Jira account email",
            },
            {
                "name": "api_key",
                "type": "secret",
                "required": True,
                "level": "org",
                "description": "Jira API token",
            },
            {
                "name": "project_key",
                "type": "string",
                "required": False,
                "level": "team",
                "description": "Default project key",
            },
        ],
    },
    {
        "id": "msteams",
        "name": "Microsoft Teams",
        "category": "communication",
        "description": "Microsoft Teams for incident notifications and collaboration",
        "docs_url": "https://docs.microsoft.com/en-us/graph/api/resources/teams-api-overview",
        "display_order": 65,
        "featured": False,
        "fields": [
            {
                "name": "webhook_url",
                "type": "secret",
                "required": True,
                "level": "team",
                "description": "Teams Incoming Webhook URL",
            },
            {
                "name": "tenant_id",
                "type": "string",
                "required": False,
                "level": "org",
                "description": "Azure AD Tenant ID (for Graph API)",
            },
            {
                "name": "client_id",
                "type": "string",
                "required": False,
                "level": "org",
                "description": "Azure AD App Client ID",
            },
            {
                "name": "client_secret",
                "type": "secret",
                "required": False,
                "level": "org",
                "description": "Azure AD App Client Secret",
            },
        ],
    },
    {
        "id": "confluence",
        "name": "Confluence",
        "category": "collaboration",
        "description": "Atlassian Confluence for runbooks and documentation",
        "docs_url": "https://developer.atlassian.com/cloud/confluence/rest/v2/",
        "display_order": 62,
        "featured": False,
        "fields": [
            {
                "name": "domain",
                "type": "string",
                "required": True,
                "level": "org",
                "description": "Confluence instance URL",
                "placeholder": "https://company.atlassian.net/wiki",
            },
            {
                "name": "email",
                "type": "string",
                "required": True,
                "level": "org",
                "description": "Confluence account email",
            },
            {
                "name": "api_key",
                "type": "secret",
                "required": True,
                "level": "org",
                "description": "Confluence API token",
            },
            {
                "name": "space_key",
                "type": "string",
                "required": False,
                "level": "team",
                "description": "Default space key",
            },
        ],
    },
    {
        "id": "loki",
        "name": "Loki",
        "category": "observability",
        "description": "Grafana Loki for log aggregation and querying with LogQL",
        "docs_url": "https://grafana.com/docs/loki/latest/",
        "display_order": 24,
        "featured": False,
        "fields": [
            {
                "name": "domain",
                "type": "string",
                "required": True,
                "level": "org",
                "description": "Loki instance URL",
                "placeholder": "https://loki.company.com:3100",
            },
            {
                "name": "api_key",
                "type": "secret",
                "required": False,
                "level": "org",
                "description": "Loki API key or Bearer token (optional for unauthenticated instances)",
            },
        ],
    },
    {
        "id": "jaeger",
        "name": "Jaeger",
        "category": "observability",
        "description": "Jaeger distributed tracing for request flow analysis",
        "docs_url": "https://www.jaegertracing.io/docs/",
        "display_order": 26,
        "featured": False,
        "fields": [
            {
                "name": "domain",
                "type": "string",
                "required": True,
                "level": "org",
                "description": "Jaeger Query API URL",
                "placeholder": "https://jaeger.company.com:16686",
            },
            {
                "name": "api_key",
                "type": "secret",
                "required": False,
                "level": "org",
                "description": "Jaeger API key or Bearer token (optional for unauthenticated instances)",
            },
        ],
    },
    {
        "id": "prometheus",
        "name": "Prometheus",
        "category": "observability",
        "description": "Prometheus for metrics querying with PromQL",
        "docs_url": "https://prometheus.io/docs/",
        "display_order": 18,
        "featured": False,
        "fields": [
            {
                "name": "domain",
                "type": "string",
                "required": True,
                "level": "org",
                "description": "Prometheus server URL",
                "placeholder": "https://prometheus.company.com:9090",
            },
            {
                "name": "api_key",
                "type": "secret",
                "required": False,
                "level": "org",
                "description": "Prometheus API key or Bearer token (optional for unauthenticated instances)",
            },
        ],
    },
]


def main():
    """Add missing integration schemas to database."""
    import json

    engine = create_engine(DATABASE_URL)
    added_count = 0
    skipped_count = 0

    try:
        with engine.begin() as conn:
            for schema in INTEGRATION_SCHEMAS:
                # Check if already exists
                result = conn.execute(
                    text("SELECT id FROM integration_schemas WHERE id = :id"),
                    {"id": schema["id"]},
                )
                if result.fetchone():
                    print(f"  [skip] {schema['id']} already exists")
                    skipped_count += 1
                    continue

                # Insert schema
                conn.execute(
                    text("""
                        INSERT INTO integration_schemas (
                            id, name, category, description, docs_url, icon_url,
                            display_order, featured, fields, created_at, updated_at
                        ) VALUES (
                            :id, :name, :category, :description, :docs_url, :icon_url,
                            :display_order, :featured, CAST(:fields AS jsonb), NOW(), NOW()
                        )
                    """),
                    {
                        "id": schema["id"],
                        "name": schema["name"],
                        "category": schema["category"],
                        "description": schema["description"],
                        "docs_url": schema.get("docs_url"),
                        "icon_url": schema.get("icon_url"),
                        "display_order": schema["display_order"],
                        "featured": schema.get("featured", False),
                        "fields": json.dumps(schema["fields"]),
                    },
                )
                print(f"  [add] {schema['id']}")
                added_count += 1

        print(f"\nDone: {added_count} added, {skipped_count} skipped")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
