# Feature Workflow

Every feature must follow this sequence.

## Step 1: Feature framing
Before writing code:
- restate feature goal
- identify affected layers/modules
- define acceptance criteria
- identify edge cases
- identify abuse/security cases
- explain how the feature fits architecture

## Step 2: Design impact
- identify interfaces/contracts used
- identify new ADRs needed
- identify data model changes if any
- identify test strategy

## Step 3: Implementation
- implement the feature as a module/slice
- preserve architecture boundaries
- add validation
- add explicit error handling
- keep code modular and testable

## Step 4: Testing
At minimum:
- unit tests for business logic
- integration tests for key interactions
- negative tests for invalid inputs
- tests for major failure paths

## Step 5: Review
Review against:
- architecture conformance
- security
- maintainability
- extensibility
- test quality
- docs completeness

## Step 6: Completion output
Provide:
- files changed
- AC to implementation mapping
- tests added
- docs/ADR updates
- known gaps or follow-ups