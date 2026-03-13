# Review Checklist

Before marking any feature complete, verify:

## Architecture
- Are boundaries preserved?
- Is business logic kept out of presentation?
- Is the feature isolated to the right module/layer?
- Is coupling minimized?

## Security
- Are inputs validated?
- Are trust boundaries respected?
- Any hardcoded secrets?
- Any injection-prone patterns?
- Any unsafe logging?

## Error handling
- Are expected failures handled?
- Are errors explicit and meaningful?
- Are timeouts/tool failures/malformed outputs addressed?

## Testing
- Are unit tests present?
- Are integration tests present where needed?
- Are negative tests present?
- Do tests map to acceptance criteria?

## Documentation
- Were relevant docs updated?
- Is a new ADR needed?
- Are assumptions and limitations recorded?

## Extensibility
- Will this make future vulnerability modules easier or harder?
- Any shortcuts that should be removed now?