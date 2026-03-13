# Architecture

## Goal
A modular security-analysis platform built incrementally, where each vulnerability capability is added as an independent feature module.

## Layers

### 1. Presentation layer
Responsibilities:
- web UI
- CLI
- API
- report rendering (PDF/JSON/etc.)

Rules:
- no business logic
- no vulnerability-specific logic
- consumes normalized outputs only

### 2. AI agent orchestration layer
Responsibilities:
- sequencing actions
- workflow progression
- dependency management between steps
- enforcing execution policy/guardrails

Rules:
- should not own vulnerability semantics
- should interact through stable interfaces

### 3. Application/domain layer
Responsibilities:
- core use cases
- domain models
- finding/evidence/result normalization
- severity/confidence/policy logic

Rules:
- central home for shared business logic
- should remain independent of presentation details

### 4. LLM layer
Responsibilities:
- model/provider abstraction
- prompt construction
- structured output parsing
- validation
- retries/timeouts/configuration

Rules:
- all LLM calls go through this layer
- no provider-specific logic elsewhere

### 5. Vulnerability checks layer
Responsibilities:
- one module per vulnerability class
- check-specific detection logic
- check-specific evidence collection
- common output contract

Rules:
- each check independently testable
- each check emits normalized result models
- checks should not depend on presentation concerns

### 6. Tool adapter layer
Responsibilities:
- wrappers around scanners, parsers, mobile tooling, external systems

Rules:
- no ad hoc direct tool integration from business logic
- tools accessed through adapters/contracts

### 7. Infrastructure layer
Responsibilities:
- persistence
- queueing
- storage
- config
- secrets
- telemetry

## Dependency rules
Allowed direction of dependency:
Presentation -> Orchestration -> Application/Domain
Application/Domain -> Vulnerability Checks / LLM / Tool Adapters / Infrastructure abstractions
Infrastructure implements required interfaces

Disallowed:
- Presentation -> vulnerability modules directly
- Presentation -> LLM directly
- Vulnerability module -> presentation layer
- arbitrary cross-module coupling between vulnerability modules