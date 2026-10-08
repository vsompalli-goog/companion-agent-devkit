---
name: aa-migration
description: Migrate legacy Google Cloud Agent Assist AI Coach (Generators with agentCoachingContext) and PGKA/GKA (Proactive Generative Knowledge Assist) configurations to Companion Agent. Use when mapping legacy AI Coach or PGKA concepts, converting Generator JSON to CompanionAgent skillConfigs, or explaining behavioral differences after migration.
---

# AI Coach & PGKA to Companion Agent Migration Skill (FR-1.3)

Use this skill whenever helping FDEs or customer engineers migrate from legacy **AI Coach** (`Generator` with `agentCoachingContext`) or **PGKA / GKA** (Proactive Generative Knowledge Assist) to **Companion Agent**.

## Quick CLI Conversion (`aa-devkit migrate-coach`)

To convert an exported legacy AI Coach `Generator` JSON into a valid Companion Agent JSON payload with automatic tool syntax rewriting (`{$tool.x}` $\rightarrow$ `{@TOOL:x}`) and `displayDetails` rescue:

```bash
aa-devkit migrate-coach ./legacy_ai_coach_generator.json --output ./migrated_companion_agent.json
```

Always run `aa-devkit review` on the migrated bundle afterward to verify Checkpoint Rule and One-Scenario-Per-Card compliance.

## Detailed Reference

Read [references/ai_coach_and_pgka_migration.md](references/ai_coach_and_pgka_migration.md) for:
1. **Concept & Schema Mapping Table** (AI Coach `agentCoachingContext` $\rightarrow$ `CompanionAgent.skillConfigs`).
2. **Step-by-Step Conversion Playbook** (rescuing rules from `displayDetails`, converting `agentAction` + `systemAction` into sequential `actions[]`, rewriting `{$tool.*}` to `{@TOOL:*}` + `cesToolSpecs`).
3. **PGKA / GKA $\rightarrow$ Companion Agent Migration** (how `AGENTIC_KNOWLEDGE_ASSIST` coexists with Guidance cards, datastore rewriter settings, and avoiding Knowledge Assist starvation).
4. **Known Behavioral Differences** between legacy AI Coach/PGKA and Companion Agent.
