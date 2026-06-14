---
title: "<Spec Title>"
status: Draft            # Draft | In Review | Approved | Superseded
version: 0.1
owners: [r-log]
related: []              # links to other specs/docs
---

# <Spec Title>

> One-paragraph elevator summary: what this is and why it exists.
> A reader should understand the whole intent from this block alone.

<!--
  SPEC STRUCTURE CONVENTION
  Every spec in docs/specs/ follows the numbered sections below, in order.
  Omit a section only if it is genuinely N/A, and say so explicitly
  ("## 9. Security — N/A, internal tool"). Keep section numbers stable so
  specs are cross-referenceable ("see §6.2"). Prose stays terse; tables and
  bullet lists over paragraphs; every claim concrete.
-->

## 1. Summary
What the system does, for whom, in 3–5 sentences.

## 2. Goals & Non-Goals
- **Goals** — bulleted, testable outcomes.
- **Non-Goals** — explicitly out of scope; prevents scope creep.

## 3. Context & Constraints
Background, current landscape, hard constraints (infra, legal, social, budget),
and any verified findings that shaped the design.

## 4. Invariants (Non-Negotiable Rules)
The decisions that are expensive/impossible to reverse. These govern every
later section and every implementation choice.

## 5. System Architecture
Components and how they connect. Distinguish **data topology** (where truth
lives) from **presentation topology** (what users see). Include a diagram.

## 6. Data Model
Entities, identity/keys, provenance, temporal handling, relationships.

## 7. Pipeline & Data Flow
The end-to-end stages, the triggers/clocks that drive them, idempotency.

## 8. Infrastructure & Deployment
Concrete hosting/product mapping, compute, storage, cost floor, what to verify.

## 9. Interfaces & Contracts
Stable contracts: ingestion shape, output/file schemas, internal seams/APIs.

## 10. Security & Access
Authn/authz, secret handling, rate-limit/politeness, data-handling posture.

## 11. Edge Cases & Failure Modes
Enumerated known traps and how the design handles each.

## 12. Phased Build Plan
Ordered phases, each independently shippable, with an explicit MVP boundary.

## 13. Open Questions & Risks
Unresolved decisions, things to verify, and their owners.

## 14. Glossary
Project-specific terms, one line each.

## 15. References
Source docs, repos, dashboards, tickets.
