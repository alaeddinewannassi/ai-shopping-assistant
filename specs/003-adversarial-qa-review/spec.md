# Feature Specification: Adversarial QA & Widget UX Review

**Feature Branch**: `003-adversarial-qa-review`

**Created**: 2026-09-12

**Status**: Draft

**Input**: User description: "make a speckit to analyse the project, launch tests and criticize
with adversary agents to have good results (in term of IA responses and help on chatbot widget
for user experience)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Adversarial review of the assistant's AI responses (Priority: P1)

As the person operating this store, I want the assistant's conversational responses put
under deliberate adversarial pressure — not just "does it work," but "can I make it lie,
guess, or break" — so that real shoppers never encounter a hallucinated price, an invented
policy, a fabricated category, or a broken confirmation before I do.

**Why this priority**: A single confidently-wrong answer (a fake discount, an invented
return policy, a made-up product) destroys shopper trust immediately and is the #1
differentiator between this assistant and a "toy" chatbot. This must be checked before
anything else.

**Independent Test**: Can be fully run today, against the live dockerized store, without
touching the widget or any other part of the system — a reviewer sends adversarial
messages to the `/chat` endpoint (hallucination bait, prompt injection, ambiguous
references, out-of-catalog questions, multi-turn context traps) and checks each reply
against the real catalog/cart/order state. Delivers a prioritized list of confirmed defects
independent of any other user story.

**Acceptance Scenarios**:

1. **Given** a fresh conversation with no prior context, **When** the shopper asks a vague,
   open-ended question (a gift idea, a "what do you have" browse), **Then** the assistant
   never states a product name, price, category, or store policy that isn't real and
   verifiable against the connected store.
2. **Given** an ordinary conversation, **When** the shopper's message contains an attempt to
   override the assistant's role, extract its instructions, or manufacture a discount/
   permission it doesn't have, **Then** the assistant declines and stays within its normal
   shopping-assistant behavior — no fabricated compliance, no leaked internal prompt.
3. **Given** a reply the assistant is about to send, **When** that reply names a specific
   product, price, stock level, or discount, **Then** that fact is traceable to a real
   adapter call made during the same turn (not invented, not stale, not guessed).
4. **Given** an ambiguous or noisy customer message (a typo, an exact product name buried in
   unrelated chatter, a reference to "it"/"the other one"), **When** the assistant resolves
   it, **Then** it either resolves to the single, correct, real product or asks a genuine
   clarifying question — it never silently guesses wrong and never gets stuck asking about
   unrelated products.

---

### User Story 2 - Adversarial review of the chat widget's user experience (Priority: P2)

As the person operating this store, I want the embeddable chat widget itself critiqued the
way a skeptical UX reviewer would — error states, loading/latency feedback, mobile
usability, accessibility, and how it recovers from a bad backend response — so shoppers
don't abandon a conversation because the *interface* let them down even when the AI's
answer was fine.

**Why this priority**: The best conversational logic is worthless if the widget hides it
behind a confusing, inaccessible, or fragile interface. This is second priority because it
depends on the widget being exercised against a backend whose answers are already trusted
(User Story 1).

**Independent Test**: Can be tested independently by loading the demo storefront with the
widget embedded and driving it through a browser (or reading its source against a UX
checklist) without needing any specific backend conversation outcome — pass/fail is about
the widget's own behavior (does it show a clear error, is the confirm/cancel affordance
obvious, does it work with only a keyboard, does it recover from a slow/failed request).

**Acceptance Scenarios**:

1. **Given** the assistant service is slow or returns an error, **When** the shopper is
   waiting on a reply, **Then** the widget shows a clear, non-technical loading/error state
   and never leaves the shopper staring at a silently-dead input box.
2. **Given** a reply that needs yes/no confirmation before a cart mutation, **When** it is
   shown in the widget, **Then** the confirm/cancel choice is visually unambiguous and
   cannot be mistaken for a plain informational message.
3. **Given** a shopper using only a keyboard or a screen reader, **When** they operate the
   widget (open it, type, send, read replies), **Then** every control is reachable and
   announced correctly.
4. **Given** a small mobile viewport, **When** the widget is open, **Then** it does not
   obscure critical page content (e.g. the checkout button) and remains fully usable.

---

### User Story 3 - Findings become a verified, prioritized backlog (Priority: P3)

As the person operating this store, I want every finding from the adversarial review
verified against the real system (not just asserted) and turned into a prioritized list —
and, where a fix is low-risk and clearly scoped, actually applied and re-verified by the
existing test suite — so the review produces improvement, not just a document.

**Why this priority**: A pile of unverified "the AI might do X" claims is close to
worthless and risks becoming its own source of noise/false alarms; this depends on User
Stories 1 and 2 having already produced raw findings to verify.

**Independent Test**: Can be tested independently by taking any single raw finding and
confirming it either (a) reproduces against the live system and gets a regression test plus
a fix, or (b) is marked "not reproduced" / "by design" with the reasoning shown — this
doesn't require the full review to be complete.

**Acceptance Scenarios**:

1. **Given** a claimed defect from the adversarial review, **When** it is triaged, **Then**
   it is independently reproduced against the live store/assistant before being reported as
   real — a finding that only reproduces once, or only in the reviewer's reasoning and not
   in an actual system response, is discarded or downgraded.
2. **Given** a confirmed, low-risk, well-scoped defect, **When** it is fixed, **Then** a
   regression test is added that fails before the fix and passes after, and the full
   existing test suite still passes.
3. **Given** the full review is complete, **When** the results are reported, **Then** they
   are grouped by severity (breaks trust/hallucination > breaks a task > UX friction) with a
   clear reproduction path for each, not an undifferentiated list.

### Edge Cases

- What happens when the LLM provider is rate-limited or briefly unavailable mid-review? The
  review MUST distinguish "the assistant behaved badly" from "the LLM call itself failed and
  the existing fallback path took over" — the latter is not a new finding if the fallback
  already behaves safely.
- What happens when an adversarial agent's own report claims a defect that doesn't actually
  reproduce against the real running system? It MUST be excluded from the final findings,
  not included with a caveat — an unverified claim in a QA report is itself a trust problem.
- What happens when a finding would require a mutating action (adding to cart, applying a
  promo, placing an order) to reproduce? The review MUST use disposable/test session IDs and
  MUST NOT be blocked from testing the confirmation-gate behavior itself (declining a
  proposal must always be a safe, available action).
- What happens when two adversarial passes report the same underlying root cause through
  different symptoms? They MUST be de-duplicated to one finding before prioritization.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The review process MUST run the project's existing automated test suites
  (backend, widget, backoffice) and treat any failure there as a precondition to fix before
  new adversarial findings are considered meaningful.
- **FR-002**: The review MUST exercise the live assistant (both configured demo tenants)
  with adversarial conversational inputs targeting: fabrication of facts not in the
  catalog/store config, prompt-injection / role-override attempts, ambiguous or noisy
  product references, and multi-turn context retention.
- **FR-003**: The review MUST verify every candidate hallucination finding by checking the
  assistant's stated fact against the real, currently-connected store data (catalog,
  pricing, stock, cart, promo rules) — never accept a finding on the reviewer's judgment
  alone.
- **FR-004**: The review MUST separately assess the chat widget's user experience
  (loading/error states, confirmation clarity, keyboard/screen-reader accessibility, mobile
  layout) independent of whether the backend's answers are correct.
- **FR-005**: The review MUST NOT perform any real, irreversible mutation against a
  non-test resource (no real order placement, no promo abuse) while probing behavior —
  cart/checkout flows are tested using disposable session ids and are stopped at the
  confirmation step unless completing them is the specific thing under test.
- **FR-006**: Every reported finding MUST include a reproduction path (the exact input and
  observed output, or the exact code location for a UX/code-level finding) sufficient for
  someone else to confirm it independently.
- **FR-007**: The review MUST classify each finding by severity (hallucination/trust-breaking,
  task-breaking, UX friction, cosmetic) and MUST NOT present them as an undifferentiated
  list.
- **FR-008**: For any finding fixed as part of this process, a regression test MUST be added
  and the full existing suite MUST remain green.
- **FR-009**: The review MUST distinguish genuine assistant misbehavior from expected
  degraded-mode behavior (e.g. LLM provider rate-limiting, store backend permission gaps
  already handled by an existing graceful-degradation path) — the latter is reported as an
  environment/configuration note, not a new defect.

### Key Entities

- **Finding**: One confirmed adversarial-review result — includes the category (AI response
  quality vs. widget UX), severity, the exact reproduction input/steps, the observed vs.
  expected behavior, and its resolution status (fixed / flagged for a product decision / not
  reproduced / by design).
- **Review Pass**: One focused adversarial run against one surface (assistant conversation
  quality, or widget UX) — produces a list of candidate findings before verification.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of findings in the final report have been independently reproduced
  against the live system (or the exact source location, for a code-level UX finding) — zero
  findings included on reasoning alone.
- **SC-002**: The existing automated test suites (backend, widget, backoffice) remain 100%
  green (excluding pre-existing environment-gated skips) throughout and after the review.
- **SC-003**: Every trust-breaking finding (a fabricated fact reaching a shopper) identified
  during the review is either fixed with a regression test, or explicitly flagged as a
  product decision, before the review is reported as complete.
- **SC-004**: The final report lets a reader go from "what's wrong" to "how to see it
  themselves" in one step for every finding, with no finding requiring the reader to trust
  an unverified claim.

## Assumptions

- The dockerized reference store (both demo tenants) and the assistant service are
  available and reachable for live testing during the review; if the LLM provider or store
  backend degrades mid-review, that is treated per the Edge Cases above, not as a blocker to
  the whole process.
- "Adversary agents" means independent review passes — one per surface (AI response
  quality, widget UX) — each producing candidate findings that are then verified against the
  real system before being reported, rather than accepted as-is.
- This review is a point-in-time pass over the current `main` branch state, not a
  continuously-running monitor; running it again later (e.g. after further changes) is a
  new instance of the same process, not an open-ended background job.
- Fixing every finding is out of scope for a single pass when a fix would require a
  significant architecture or product decision (e.g. adding true multi-item cart proposals,
  or semantic/embedding-based search) — those are reported as recommendations, not applied.
