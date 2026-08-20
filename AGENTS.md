# Shelf AGENTS Instructions

## Project

Shelf is an established self-hosted library application with active production deployments.

This repository may be used as the source for customized deployments, so changes must preserve existing user data and working behavior unless the task explicitly requires otherwise.

## Production Safety

Never assume repository HEAD is identical to any currently deployed production version.

Never deploy a branch merely because it builds successfully.

Before consequential production deployment:

1. identify the currently deployed version or image;
2. identify the target version or image;
3. confirm database and schema compatibility;
4. create or verify an appropriate backup;
5. establish a rollback procedure;
6. test locally;
7. test in staging when appropriate;
8. verify important behavior after deployment.

Production data must never be treated as disposable development data.

Do not perform destructive or difficult-to-reverse data operations without explicit approval.

## Existing Working Behavior

Preserve verified behavior unless the task intentionally changes it.

Important areas include:

- metadata-provider fallback;
- Open Library integration;
- Hardcover integration;
- Google Books integration;
- browse pagination, filtering, sorting, and search;
- barcode scanning;
- Store Mode and PWA behavior;
- authentication and sessions;
- HTTPS;
- persistent SQLite data;
- cover and file storage;
- mobile workflows;
- library locations;
- backup and restore.

Understand existing fixes before rewriting or replacing them.

## Architecture

Inspect the current repository before making assumptions.

The established application architecture includes technologies such as:

- Python
- FastAPI
- SQLite
- Jinja2
- HTMX
- Alpine.js
- Tailwind CSS
- Docker

Prefer the existing architecture when it remains suitable.

Do not replace stable components merely because another technology is newer or personally preferred.

Architecture changes require a material benefit that justifies migration cost, regression risk, and maintenance impact.

## Scope Discipline

For established behavior, prefer the smallest safe change that fully solves the problem.

Before modifying code:

1. inspect the relevant implementation;
2. inspect related tests;
3. inspect relevant documentation;
4. understand current behavior;
5. identify regression risks;
6. make the smallest appropriate change.

Do not combine unrelated redesign, cleanup, refactoring, or feature work into a scoped fix without a concrete reason.

## Audit Work

Repository audits may inspect broadly, but discovery and modification are separate phases.

Do not automatically fix every issue discovered during an audit.

Classify and prioritize findings first.

Useful classifications include:

- confirmed defect;
- probable defect;
- security issue;
- data-safety issue;
- performance issue;
- UX issue;
- technical debt;
- maintainability issue;
- enhancement opportunity;
- no action required.

For meaningful findings, record:

- evidence;
- affected area;
- practical impact;
- severity;
- confidence;
- recommended action;
- verification required;
- regression risk.

## Feature Work

New features should integrate cleanly with established Shelf behavior.

Before substantial implementation, define:

- user need;
- expected behavior;
- non-scope;
- data or schema impact;
- security impact;
- mobile and PWA impact;
- migration impact;
- testing requirements;
- deployment impact.

Prefer incremental and independently testable features.

## Testing

Execution is not proof of correctness.

Run applicable checks such as:

- automated tests;
- targeted regression tests;
- pytest;
- application startup checks;
- Docker build;
- Docker startup;
- database and migration checks;
- security-sensitive tests;
- relevant manual workflows.

For UI changes, verify relevant desktop and mobile behavior.

For scanning or PWA changes, explicitly test the affected workflow.

Do not claim a check passed unless it actually ran successfully.

## Persistent Data

Persistent application data must remain outside disposable containers.

Schema changes require migration and rollback consideration.

Never expose or commit:

- passwords;
- API keys;
- tokens;
- encryption keys;
- private certificates;
- production environment files;
- production database contents.

Use safe placeholders in examples and documentation.

## Staging and Deployment

Recommended deployment flow:

local development
->
Git/GitHub
->
staging
->
acceptance testing
->
backup or snapshot
->
production
->
post-deployment verification

Keep staging and production data separate.

Production deployment is complete only after important workflows and persistent data are verified.

## Completion

A change is complete only when:

- requested behavior is implemented;
- relevant tests and checks pass;
- regression risk has been considered;
- the final diff is reviewed;
- accidental changes are removed;
- secrets and private deployment details are absent;
- documentation is updated when necessary;
- data and deployment implications are understood.

Production changes additionally require appropriate staging, backup, deployment verification, and rollback readiness.