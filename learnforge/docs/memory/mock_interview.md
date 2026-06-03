# Mock Interview Memory Pack

## Purpose

This file expands the mock interview material into a structured reference pack. It is meant for manual review, future seeding, and prompt grounding. Runtime daily memory should still use `data/memory/YYYY-MM-DD.md`.

## Interview Objectives

- Expose shallow memorization by asking follow-up questions tied to the candidate's previous answer.
- Convert every weak answer into a concrete weak point, evidence quote, and next drill.
- Keep the interview bounded: one current question, one answer, one score card, then either follow up or move on.
- Prefer engineering tradeoffs over textbook recitation.
- Preserve session context so refresh, side questions, and handoff do not erase the current question.
- Separate roles: Interviewer asks, Judge scores, Strategist decides next action, Coach summarizes.

## Session State Fields

| Field | Meaning | Example |
|---|---|---|
| `topic` | Main interview area | Redis, concurrency, database |
| `current_question` | Question waiting for answer | Explain cache penetration and defenses. |
| `turn_index` | Completed answer rounds | 3 |
| `turn_scores` | Per-round judge scores | correctness/depth/clarity |
| `risk_flags` | Answer risks | vague, overclaim, no_evidence |
| `topic_coverage` | Topics touched so far | Redis persistence, cache avalanche |
| `handoff_summary` | Context sent to Manager on escalation | Mock exposed weak Redis consistency reasoning |

## Action Classification

| User Input Type | Expected Action | Notes |
|---|---|---|
| Clear technical answer | `answer` | Score and continue. |
| "顺便问..." / explicit question | `side` | Answer via QA/RAG; do not advance mock turn. |
| "今天先这样吧" | `exit` | Ask for confirmation before settlement. |
| "先别，再来一题" after confirmation | `continue` | Cancel exit and restore current question. |
| "帮我诊断弱点并改计划" | `escalate` | Leave mock and run diagnosis/planning with handoff. |
| "稍等" / "暂停一下" | `pause` | Preserve checkpoint and current question. |

## Scoring Rubric

| Dimension | 1 | 3 | 5 |
|---|---|---|---|
| Correctness | Factually wrong or contradicts core concept | Mostly correct but missing edge cases | Correct, precise, and handles caveats |
| Depth | Only definitions | Mentions mechanism and one tradeoff | Explains mechanism, tradeoffs, failure modes, and examples |
| Clarity | Disorganized or vague | Understandable but generic | Interview-ready, structured, concise |
| Evidence | No evidence or project tie-in | One generic example | Specific project/metric/decision evidence |

## Risk Flags

- `vague`: answer uses broad words like "optimize", "improve", "ensure" without mechanism.
- `no_evidence`: answer claims experience but gives no concrete action, metric, or incident.
- `overclaim`: answer states absolute guarantees for eventually consistent or probabilistic systems.
- `missing_tradeoff`: answer lists solution but not cost.
- `missing_boundary`: answer does not say when the solution fails.
- `memorized_only`: answer sounds like a definition and cannot survive a follow-up.
- `wrong_layer`: answer mixes application, database, cache, and network layers incorrectly.
- `unsafe_default`: answer proposes a risky production default without constraints.

## Topic Bank

### Redis

1. Explain RDB, AOF, and mixed persistence. When would you choose each?
2. What happens during AOF rewrite? Why does it not block normal writes completely?
3. Explain cache penetration, breakdown, and avalanche with different defenses.
4. How do you keep cache and database eventually consistent?
5. Why is "update DB then delete cache" often preferred over "update cache"?
6. How does Redis implement expiration and eviction?
7. Explain Redis single-threaded command execution and why it can still be fast.
8. What are slow query, big key, and hot key? How do you detect each?
9. How would you design distributed locking with Redis? What are the failure modes?
10. What changes when Redis is deployed with Sentinel or Cluster?

### Concurrency

1. Compare optimistic lock and pessimistic lock with real usage boundaries.
2. Explain CAS and ABA. How can ABA be avoided?
3. What does `volatile` guarantee and not guarantee?
4. Explain `synchronized` vs `ReentrantLock`.
5. Walk through `ThreadPoolExecutor` task submission.
6. How do you choose queue type, pool size, and rejection policy?
7. What causes deadlock? How do you prevent and diagnose it?
8. Explain memory visibility, happens-before, and instruction reordering.
9. How do you design idempotent concurrent writes?
10. What metrics would you watch for thread pool saturation?

### Database

1. Why does MySQL commonly use B+ tree indexes?
2. Explain clustered index vs secondary index.
3. What is index covering? What is back-to-table lookup?
4. List common index invalidation scenarios.
5. Explain MVCC with undo log and ReadView.
6. Compare RC and RR isolation in MySQL.
7. What is a gap lock and when does it appear?
8. How do you investigate a slow SQL query?
9. How would you design pagination for deep pages?
10. Explain transaction deadlock diagnosis.

### JVM

1. Describe JVM runtime memory areas.
2. Explain object allocation path and TLAB.
3. Compare young GC and full GC symptoms.
4. Explain G1 region model and pause target.
5. What causes memory leak in Java services?
6. How do you read GC logs at a high level?
7. Explain class loading and parent delegation.
8. What is metaspace and why can it OOM?
9. How do you troubleshoot high CPU in a Java process?
10. Explain common thread dump states.

### Network

1. Why TCP needs three-way handshake and four-way close.
2. Explain TIME_WAIT and why it exists.
3. Compare HTTP and HTTPS handshake cost.
4. Explain TCP congestion control phases.
5. What happens when DNS resolution is slow?
6. How do keep-alive and connection pools affect latency?
7. Explain idempotency in HTTP APIs.
8. How do retries cause traffic amplification?
9. What is backpressure?
10. How would you debug p95 latency spike?

### System Design

1. Design a rate limiter. Compare fixed window, sliding window, and token bucket.
2. Design a feed timeline. Compare push, pull, and hybrid.
3. Design URL shortener with uniqueness and hot key handling.
4. Design delayed message delivery.
5. Design order creation with idempotency and inventory consistency.
6. Design distributed job scheduling.
7. Design search autocomplete.
8. Design metrics ingestion and alerting.
9. Design file upload with resumable chunks.
10. Design notification delivery with retry and dedupe.

## Follow-up Patterns

- "You mentioned X. What exactly happens when X fails?"
- "What metric would prove your solution works?"
- "What is the cost of this approach?"
- "Give me a concrete production incident where this matters."
- "How would your answer change at 10x traffic?"
- "What does the database/cache/network layer see during this flow?"
- "Which part is guaranteed and which part is best effort?"
- "What would you log to debug it?"
- "How would you test this behavior?"
- "What is the simplest version you would ship first?"

## Answer Card Template

```text
Question:
Answer summary:
Score:
- correctness:
- depth:
- clarity:
Risk flags:
Evidence from answer:
Missing points:
Best next follow-up:
One drill:
```

## Good Answer Shape

1. State the core idea in one sentence.
2. Explain the mechanism in two to four steps.
3. Name one tradeoff or failure mode.
4. Give one concrete project or production example.
5. End with when you would choose or avoid it.

## Weak Answer Signals

- Uses only definitions and cannot explain execution flow.
- Mentions a tool but not the condition for using it.
- Gives a solution without discussing consistency, latency, or operational cost.
- Claims "guaranteed consistency" for cache/database dual writes.
- Says "Redis is single-threaded so no concurrency issue" without I/O, command atomicity, and deployment caveats.
- Says "use thread pool" without queue, rejection policy, saturation metrics, or task type.
- Says "add index" without cardinality, selectivity, covering, and query plan.
- Says "use MQ" without retry, dedupe, ordering, and poison message handling.

