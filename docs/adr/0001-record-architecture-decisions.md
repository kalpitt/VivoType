# [ADR-0001] Record Architecture Decisions

**Status:** Accepted  
**Date:** 2026-06-15  

## Context
VivoType is a complex project involving both a native macOS Swift layer and a heavy Python machine learning backend. Over time, we have made several critical architectural decisions (e.g., how to bundle the app, how to communicate between Swift and Python, and how to avoid C++ dependency bloat). 

As AI agents increasingly assist with development, they often suggest industry-standard tools (like `PyInstaller`) that actually break our specific edge cases (like Apple Gatekeeper quarantines on ML models). We need a standardized way to document *why* we made past decisions to prevent agents from unintentionally regressing the architecture.

## Decision
We will use Markdown Architectural Decision Records (ADRs) to document significant design choices.
Crucially, every ADR must include a specific `## 🤖 Agent Directives` section at the bottom. This section provides strict, parseable "DO" and "DO NOT" rules for AI coding assistants.

## Consequences
- Better onboarding for both human developers and AI agents.
- Agents are explicitly instructed by `CLAUDE.md` to read the ADR index before proposing massive architectural shifts.
- Slight overhead in maintaining documentation.

## 🤖 Agent Directives
- **DO NOT** propose major architectural changes or new packaging tools without first reading the `docs/adr/README.md` index to ensure it doesn't conflict with past decisions.
- **DO** create a new ADR when a significant architectural shift is proposed and accepted.
