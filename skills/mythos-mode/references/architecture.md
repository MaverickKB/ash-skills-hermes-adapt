# Architecture and module boundaries

Use when introducing a module, expanding a feature, or breaking apart a god file.

## How to choose a boundary

- Group by capability or feature and by what changes together, not by arbitrary utility categories or maximum line counts.
- Each module answers one coherent question and has one primary reason to change.
- Keep private helpers near their callers until reuse is demonstrated. Do not make global `utils.ts`, `helpers.ts`, `common.ts`, or `manager.ts` dumping grounds.
- Depend on small explicit contracts. Keep infrastructure at the edges, domain decisions in the centre, and transport-specific formatting at boundaries when the application's complexity warrants the separation.
- Prefer composition and plain functions. Use classes when identity, lifecycle, encapsulated state, or framework conventions justify them.
- Only create barrel exports when they make a deliberate public API clearer. Avoid circular re-export chains.

## Example: an appropriately sized feature

```text
src/
  features/
    orders/
      order.routes.ts         HTTP mapping and transport errors
      order.schema.ts         External input validation
      order.service.ts        Domain-level orchestration and policies
      order.repository.ts     Persistence queries
      order.types.ts          Shared feature contracts
      order.service.test.ts   Behavioural tests
  shared/
    db/                     Database setup and transaction primitives
    errors/                 Cross-cutting error contracts
  app.ts                    App wiring, not business logic
```

Do not copy this tree mechanically. A tiny service may need only `orders.ts` and `orders.test.ts`. A mature service may need further subfeatures. The primary rule is cohesion.

## Red flags that require restructuring

- A route handler validates requests, authorises users, implements pricing rules, writes SQL, sends email, and maps API responses inline.
- One class exposes dozens of unrelated methods or changes for unrelated features.
- A module has enormous import lists, many unrelated exports, extensive conditional branching by feature, or mixes unrelated levels of abstraction.
- An edit to one feature frequently breaks another because of shared mutable state or bidirectional dependencies.
- Many tests require stubbing most of the application just to exercise one rule.

## Decision criteria

1. Start with the simplest boundary that preserves local reasoning.
2. Split by independent responsibility, lifecycle, ownership, security boundary, or scaling requirement.
3. Do not split to satisfy a numerical threshold alone.
4. Prefer a small explicit API to a large, configurable abstraction.
5. Keep pure rules separate from I/O if that separation yields simpler tests.
6. Prevent cyclic dependencies and ensure only the owning layer mutates its data.
7. Document architectural decisions in a concise design note when the tradeoff is consequential, not in large in-code comments.

## Engineering quality without overengineering

- No repository-wide rewrites for a local bug.
- No new microservice, event bus, state machine, ORM, or dependency injection container without a concrete need.
- No duplicate logic disguised by slightly different names.
- No shared abstractions based solely on superficial similarity. Extract after actual reuse emerges.
- Accept a slightly larger cohesive module over many tiny files that make the control flow harder to follow.
