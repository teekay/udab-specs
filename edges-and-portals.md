# Talk Track Edges, Entrypoints, and Portals

Design note for adding "edges that link to entrypoints" to the Talk Track UI,
and the rethinking of what an *entrypoint* actually is.

## Background

Talk Tracks are modelled as a directed graph `G_flow = (V, E)`:

- `V` = `sp_talk_track_node` rows (questions, objections, recaps, closes, portals).
- `E` = `sp_talk_track_edge` rows (answers connecting `from_id → to_id`).

Three node-level concepts were in play:

- **Regular nodes** — participate in the normal flow.
- **Portals** (`node_type='portal'`) — floating buttons outside the linear
  flow. Validated to have no incoming edges
  (`talk_track_graph.py:create_edge` rejects portal targets). Each portal
  carries a single outgoing edge to a real target node; at render time
  `_build_questions_from_full_track` pops portals out of the node map and
  the client exposes them as jump buttons.
- **Entrypoints** — the "start of a conversation type" (`conversation_type`
  column on the node, unique per `(talk_track_id, conversation_type)`).
  Surfaced to the SDR as tabs.

The working assumption — never enforced in code — was:
**entrypoint = node with in-degree 0 in `G_flow`**. Portals also have
in-degree 0 by validator rule, so both kinds of "start" were structurally
sources of the graph.

## The ask

Business wants to add an answer-like affordance on a question that "jumps"
to an entrypoint (e.g. mid-call, pivot from the current conversation type
into "Gatekeeper Intro"). In the UI this reads like an edge; in the data
model — under the old definition — it can't be an edge, because an
entrypoint "cannot have an incoming edge."

## The apparent contradiction

Formally: let `G_flow = (V, E)` and define entrypoints as the sources
`S = { v ∈ V : in-degree(v) = 0 }`. Any edge `(u, s)` with `s ∈ S` gives
`in-degree(s) ≥ 1`, so `s ∉ S`. In one graph you cannot have both.

Three ways out were considered.

### Option (i) — separate `E_restart` relation

Keep the entrypoint-as-source definition. Store the new "jump" affordances
in a sibling table (e.g. `sp_talk_track_restart`) so they never appear in
`sp_talk_track_edge`. `G_flow` remains a DAG-with-sources; the invariant is
preserved and becomes enforceable at the DB level.

**Tradeoffs**: cleanly preserves the existing model but introduces
structural duplication — the sibling table mirrors ~80% of the edge
table's columns (`from_id`, `answer_id`, `answer_text`, `sort_order`,
`disabled`, `parent_id`). Cross-relation uniqueness of `answer_id` is
awkward; every future feature on edges (bulk edit, diffing, draft/publish)
needs a mirror on restarts. Long-term drift risk is the main cost.

### Option (ii) — entrypoint as a first-class handle

Introduce `sp_talk_track_entrypoint (talk_track_id, conversation_type,
first_node_id)`. The `conversation_type` column moves off the node.
"Entrypoint" becomes a named handle; the node it points at is unconstrained
(may or may not be a source). Restarts reference `target_entrypoint_id` as
a real FK.

**Tradeoffs**: more principled, gains capabilities we don't currently have
(multiple handles per node, per-track labels, strongly-typed restart refs).
But it's substitutive: migrate existing tagged nodes, rework triage, the
Vue editor's "Conversation Type" field, session validation, import/export,
and tests. And it **still needs a restart relation on top** — Option (ii)
is effectively "(i) + first-class handles", a strict superset of work.

### Option (iii) — redefine what an entrypoint is

The other two engineer around a definition we chose, not one graph theory
imposes on us. Drop the overload, redefine:

> **Entrypoint** = node with `conversation_type` set. That's it. In-degree
> is not part of the definition.

This is the standard move in automata theory: start states in an NFA /
Mealy / Moore machine are a labeling `S ⊆ Q`, not a structural property.
Transitions can enter start states; the machine simply allows runs to
begin from any `s ∈ S`. Labeled directed graphs are a completely standard
object — there is no graph-theoretic violation.

Under this definition:

- `G_flow = (V, E)` is just a directed graph.
- `S = { v ∈ V : conversation_type(v) ≠ NULL }` is the labeled start set.
- The feature "edge to entrypoint" collapses into "regular edge." There
  was never a rule to violate — the rule was self-imposed.

**Portals stay orthogonal.** Their "no incoming edges" property is a
`node_type='portal'` validation motivated by UX (floating buttons), not by
entrypoint semantics. Under (iii), portals remain graph-theoretic sources;
entrypoints are labeled nodes that may or may not be sources. The two
concepts decouple cleanly.

## Decision

**Adopt Option (iii).** Reasons:

1. **Graph-theoretically cleaner, not fuzzier.** The previous definition
   overloaded a structural property (source) with a semantic one
   (designated start). Separating them matches standard automata-theoretic
   practice.
2. **Zero migration.** `conversation_type` already *is* the labeling
   predicate. `_extract_entry_points`, `triage_entry_points`, tab rendering,
   session `entry_node_id`, and the unique
   `(talk_track_id, conversation_type)` constraint all already work under
   the redefined semantics.
3. **The feature falls out.** An "edge to an entrypoint" is just an edge.
   No sibling table, no nullable `to_id`, no new handle concept, no
   parallel edit UI.
4. **No dormant invariant to drift.** The "entrypoints have no incoming
   edges" rule was tribal knowledge — never enforced by schema or
   validator. Option (iii) stops pretending it was ever a structural
   invariant.

## What's left open

One product decision remains: the UX behavior of an edge that lands on an
entrypoint.

- **Flow-through semantics.** SDR follows the edge; the session walk
  continues through the entrypoint node. Zero code change — it is a
  regular edge. Correct mental model: "I'm pivoting the conversation and
  the next node happens to also be marked as a start of a conversation
  type."
- **Restart semantics.** SDR abandons the current walk; fresh start from
  the entrypoint. Mirrors today's `jumpToPortal` behavior
  (`talk_track.js:421` clears `selectedAnswers`). Implementable as a
  boolean on the edge (e.g. `is_restart`) plus a small client-side branch.
  No schema reorganization, no new relation.

Both are trivially additive on top of the redefinition. Pick based on what
the business actually means by "jump."

## Implications to carry forward

- **Remove the "entrypoint has no incoming edges" claim from product
  docs and any onboarding material.** It was never a code invariant; the
  redefinition retires it explicitly.
- **Consider whether to enforce the opposite direction.** Under (iii),
  nothing prevents giving a node `conversation_type = gk_intro` and
  leaving it with in-degree 0, in-degree 1, or in-degree 10. That's the
  point. No validation to add.
- **Portal rules stand.** `node_type='portal'` validator in
  `talk_track_graph.py:142` continues to reject portal edge targets. This
  is a UX rule about floating-button nodes, unrelated to entrypoints.
- **Mermaid diagrams** (`MermaidDiagram.vue`) may now draw arrows
  entering entrypoint nodes. That's correct under the new model; no
  visual special-case needed.
- **Analytics** — arriving at an entrypoint via an edge and arriving via
  a tab click are two different session events. Tab clicks create a new
  session with `entry_node_id`; edge follows are regular session actions.
  If restart semantics is chosen for the new feature, the restart should
  probably create a new session (same as a tab click), not a session
  action — TBD with product.
