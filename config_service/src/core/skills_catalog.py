"""
Built-in skills catalog metadata.

This module contains static metadata for all built-in skills available in the agent.
Skills are domain-specific knowledge and methodologies that agents can load on-demand.

Each skill includes:
- id: Unique skill identifier (matches SKILL.md frontmatter name)
- name: Human-readable skill name
- description: What the skill provides
- category: Skill category for organization
- required_integrations: List of integration IDs this skill requires
"""

from typing import Any, Dict, List

# === BEGIN GENERATED CATALOG ===
BUILT_IN_SKILLS_METADATA = [
    {
        "id": "alerting-context",
        "name": "Alerting Context",
        "description": "Pull incident context from alerting platforms (PagerDuty). Use when investigating who's on-call, incident history, alert patterns, or MTTR metrics.",
        "category": "Alerting & On-call",
        "required_integrations": ["pagerduty"],
    },
    {
        "id": "database-postgresql",
        "name": "PostgreSQL",
        "description": "PostgreSQL database inspection and queries. Use when investigating table schemas, running queries, checking locks, replication status, or long-running queries.",
        "category": "Databases",
        "required_integrations": ["postgresql"],
    },
    {
        "id": "infrastructure",
        "name": "Infrastructure Debugging",
        "description": "Infrastructure debugging for Kubernetes. Use when investigating pod crashes, deployment issues, resource problems, or container failures.",
        "category": "Core Methodology",
        "required_integrations": [],
    },
    {
        "id": "infrastructure-azure",
        "name": "Azure",
        "description": "Azure cloud infrastructure inspection. Use when investigating Azure VMs, AKS clusters, Log Analytics (KQL), Monitor metrics/alerts, Cost Management, or NSG rules.",
        "category": "Infrastructure & Cloud",
        "required_integrations": ["azure"],
    },
    {
        "id": "infrastructure-docker",
        "name": "Docker",
        "description": "Docker container debugging and management. Use when investigating container issues, checking logs, resource usage, or Docker Compose services.",
        "category": "Infrastructure & Cloud",
        "required_integrations": ["docker"],
    },
    {
        "id": "infrastructure-kubernetes",
        "name": "Kubernetes",
        "description": "Kubernetes debugging methodology and scripts. Use for pod crashes, CrashLoopBackOff, OOMKilled, deployment issues, resource problems, or container failures.",
        "category": "Infrastructure & Cloud",
        "required_integrations": ["kubernetes"],
    },
    {
        "id": "infrastructure-neo4j",
        "name": "Neo4j",
        "description": "Query the OpenSRE knowledge graph for service topology, dependencies, and blast radius. Use after you identify an affected service or deployment — not on vague initial alerts alone.",
        "category": "Infrastructure & Cloud",
        "required_integrations": ["neo4j"],
    },
    {
        "id": "investigate",
        "name": "Investigation Methodology",
        "description": "Systematic incident investigation methodology. Use when investigating production issues, service degradation, errors, latency spikes, or outages. Provides 5-phase framework for evidence-based root cause analysis.",
        "category": "Core Methodology",
        "required_integrations": [],
    },
    {
        "id": "knowledge-base",
        "name": "Knowledge Base",
        "description": "Search runbooks, documentation, and knowledge base articles from Confluence. Use when looking for incident response procedures, service documentation, post-mortems, or troubleshooting guides.",
        "category": "Docs & Knowledge",
        "required_integrations": ["confluence"],
    },
    {
        "id": "knowledge-raptor",
        "name": "RAPTOR Knowledge",
        "description": "Search the RAPTOR knowledge base for runbooks, past incidents, service dependencies, and accumulated team knowledge. Use BEFORE Confluence when investigating incidents — this contains curated, structured knowledge that the system has learned from past investigations.",
        "category": "Docs & Knowledge",
        "required_integrations": [],
    },
    {
        "id": "memory-search",
        "name": "Memory Search",
        "description": "Search OpenSRE memory for past investigations similar to what you are seeing now. Use after you have concrete evidence (error message, failing component, stack trace)—not on vague initial alerts alone.",
        "category": "Memory",
        "required_integrations": [],
    },
    {
        "id": "metrics-analysis",
        "name": "Metrics Analysis",
        "description": "Prometheus/Grafana metrics analysis and PromQL queries. Use when investigating latency, error rates, resource usage, or any time-series metrics.",
        "category": "Observability",
        "required_integrations": ["prometheus", "grafana"],
    },
    {
        "id": "observability",
        "name": "Observability Methodology",
        "description": "Log, metric, and trace analysis methodology. Use when analyzing logs, investigating errors, querying metrics, or correlating signals across observability backends (Loki, Prometheus, Grafana, traces).",
        "category": "Core Methodology",
        "required_integrations": [],
    },
    {
        "id": "observability-grafana",
        "name": "Grafana",
        "description": "Grafana dashboard and metrics analysis. Use when querying dashboards, panels, Prometheus metrics via Grafana, checking datasources, reviewing alerts, or creating dashboards from templates.",
        "category": "Observability",
        "required_integrations": ["grafana"],
    },
    {
        "id": "observability-jaeger",
        "name": "Jaeger",
        "description": "Jaeger distributed tracing analysis. Use when investigating request latency, tracing errors across services, finding slow spans, or understanding service dependencies.",
        "category": "Observability",
        "required_integrations": ["jaeger"],
    },
    {
        "id": "observability-loki",
        "name": "Loki",
        "description": "Grafana Loki log analysis using LogQL. Use when querying or aggregating logs stored in Loki.",
        "category": "Observability",
        "required_integrations": ["loki"],
    },
    {
        "id": "platform-argocd",
        "name": "Argo CD",
        "description": "Argo CD application inspection, sync, rollback, restart, and diff via CLI. Prod/stg apps have app-name echo gates for state changes; destructive verbs blocked. Use when investigating GitOps deploy failures, out-of-sync apps, or rollout issues.",
        "category": "Other Integrations",
        "required_integrations": ["argocd"],
    },
    {
        "id": "project-jira",
        "name": "Jira",
        "description": "Jira issue tracking and incident management. Use when creating, searching, or updating Jira issues. Supports JQL queries for incident ticket analysis and alert fatigue tracking.",
        "category": "Ticketing & Project",
        "required_integrations": ["jira"],
    },
    {
        "id": "remediation",
        "name": "Remediation Actions",
        "description": "Safe remediation actions for Kubernetes. Use when proposing or executing pod restarts, deployment scaling, or rollbacks. Always use dry-run first.",
        "category": "Core Methodology",
        "required_integrations": [],
    },
    {
        "id": "runtime-config-flagd",
        "name": "flagd Feature Flags",
        "description": "Feature flag management via flagd (OpenFeature). Use to list, inspect, and toggle feature flags in the OTel Demo environment. Flags control incident injection scenarios (payment failures, CPU spikes, memory leaks, etc.) and can be toggled for remediation.",
        "category": "Other Integrations",
        "required_integrations": ["config-flagd"],
    },
    {
        "id": "streaming-kafka",
        "name": "Kafka",
        "description": "Kafka topic and consumer group management. Use when investigating Kafka topics, consumer lag, broker health, or consumer group status.",
        "category": "Other Integrations",
        "required_integrations": ["kafka"],
    },
    {
        "id": "vcs-github",
        "name": "GitHub",
        "description": "GitHub repos, pull requests, Actions workflows, commits, and issues via gh CLI. Safe read-before-write workflow. Use when investigating PR/CI failures, deployment regressions, or repository state on GitHub.",
        "category": "Code & Version Control",
        "required_integrations": ["github"],
    },
]
# === END GENERATED CATALOG ===


def get_built_in_skills() -> List[Dict[str, Any]]:
    """
    Get list of all built-in skills.

    Returns:
        List of skill metadata dicts with id, name, description, category, source, required_integrations
    """
    return [
        {
            **skill,
            "source": "built-in",
        }
        for skill in BUILT_IN_SKILLS_METADATA
    ]


def get_skills_by_integration(integration_id: str) -> List[Dict[str, Any]]:
    """
    Get all skills that require a specific integration.

    Args:
        integration_id: The integration ID (e.g., "grafana", "kubernetes", "pagerduty")

    Returns:
        List of skill metadata dicts that require this integration
    """
    return [
        {**skill, "source": "built-in"}
        for skill in BUILT_IN_SKILLS_METADATA
        if integration_id in skill.get("required_integrations", [])
    ]


def get_skills_catalog() -> Dict[str, Any]:
    """
    Get complete skills catalog.

    Returns:
        Dict with 'skills' list and 'count'
    """
    skills = get_built_in_skills()

    return {
        "skills": skills,
        "count": len(skills),
    }
