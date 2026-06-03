# Weak Points Memory Pack

## Purpose

This file expands weak-point knowledge into structured, actionable points. Each item can become a `weak` daily memory note, a diagnosis cluster, or a planning input.

## Weak Point Schema

```text
id:
topic:
symptom:
evidence:
root_cause:
drill:
success_criteria:
priority:
```

## High Priority Weak Points

### WP-01 Redis Persistence Tradeoffs

- topic: Redis
- symptom: Can name RDB/AOF but cannot explain data-loss window, rewrite, recovery speed, or mixed persistence.
- evidence: Answer says "RDB is snapshot, AOF is log" and stops there.
- root_cause: Memorized feature list without operational tradeoffs.
- drill: Compare `save`, `appendfsync everysec`, AOF rewrite, and mixed persistence in a failure timeline.
- success_criteria: Can explain what data may be lost after process crash, machine crash, and AOF rewrite.
- priority: high

### WP-02 Cache Consistency

- topic: Redis
- symptom: Says "update DB and cache together" without race analysis.
- evidence: No mention of delete-cache pattern, binlog, delayed double delete, or eventual consistency window.
- root_cause: Treats cache as source of truth.
- drill: Draw two concurrent requests: read miss and write update. Explain stale cache race.
- success_criteria: Can justify update DB then delete cache, and name when binlog async invalidation is useful.
- priority: high

### WP-03 Cache Penetration, Breakdown, Avalanche

- topic: Redis
- symptom: Mixes up the three cache failure modes.
- evidence: Uses "cache avalanche" for a single hot key expiration.
- root_cause: Terms learned as a list, not by traffic pattern.
- drill: For each mode, state trigger, traffic shape, blast radius, and defense.
- success_criteria: Can map Bloom filter/cache null, mutex/logical expiration, random TTL/multilevel cache correctly.
- priority: high

### WP-04 Thread Pool Saturation

- topic: Concurrency
- symptom: Knows core parameters but cannot reason about queue growth, rejection, or backpressure.
- evidence: Answer lists `corePoolSize`, `maxPoolSize`, `queue` only.
- root_cause: API-level memory without runtime behavior.
- drill: Trace task submission under core threads full, queue full, and max threads full.
- success_criteria: Can explain why unbounded queues hide saturation and how to monitor active count, queue size, and rejection rate.
- priority: high

### WP-05 CAS and ABA

- topic: Concurrency
- symptom: Says CAS is atomic but misses ABA and retry cost.
- evidence: No mention of version stamp or `AtomicStampedReference`.
- root_cause: Understands optimistic lock at slogan level.
- drill: Build A-B-A timeline and explain why compare succeeds incorrectly.
- success_criteria: Can connect version number, timestamp, or stamped reference to ABA prevention.
- priority: medium

### WP-06 Volatile Boundary

- topic: Concurrency
- symptom: Claims `volatile` guarantees thread safety.
- evidence: No distinction among visibility, ordering, and atomicity.
- root_cause: Missing Java Memory Model boundaries.
- drill: Explain `i++` with volatile and why it is still not atomic.
- success_criteria: Can name visibility and happens-before, and say when lock/CAS is needed.
- priority: high

### WP-07 B+ Tree Range Query

- topic: Database
- symptom: Says "B+ tree is faster" without disk-page reasoning.
- evidence: No fanout, tree height, leaf linked list, or covering index.
- root_cause: Missing storage-engine mental model.
- drill: Compare binary tree, B tree, and B+ tree under disk IO.
- success_criteria: Can explain why non-leaf nodes storing only keys increases fanout.
- priority: high

### WP-08 MVCC and Isolation

- topic: Database
- symptom: Can define MVCC but cannot explain ReadView visibility.
- evidence: No active transaction list, min trx id, max trx id, undo chain, or snapshot timing.
- root_cause: Treats MVCC as "read without lock" only.
- drill: Given two transactions, decide which row version is visible under RC and RR.
- success_criteria: Can explain why RC creates a new ReadView per statement and RR per transaction.
- priority: high

### WP-09 Index Invalidation

- topic: Database
- symptom: Lists a few cases but cannot reason from leftmost prefix and optimizer choices.
- evidence: Says "like causes index invalidation" without distinguishing `abc%` and `%abc`.
- root_cause: Rule memorization without query plan reading.
- drill: Predict whether index is used for function, implicit conversion, range, order by, and OR.
- success_criteria: Can use `EXPLAIN` fields to verify rather than guessing.
- priority: medium

### WP-10 JVM GC Diagnosis

- topic: JVM
- symptom: Can list collectors but cannot debug high latency or OOM.
- evidence: No GC log, heap dump, thread dump, or allocation rate plan.
- root_cause: Collector taxonomy not connected to production symptoms.
- drill: Given p99 latency spike, outline GC log checks and heap analysis.
- success_criteria: Can distinguish memory leak, allocation churn, full GC, and metaspace pressure.
- priority: medium

### WP-11 TCP State and TIME_WAIT

- topic: Network
- symptom: Knows handshake count but not why states exist.
- evidence: Cannot explain delayed packets, full duplex close, or 2MSL.
- root_cause: Sequence diagrams memorized without reliability purpose.
- drill: Draw close sequence and explain active closer TIME_WAIT.
- success_criteria: Can connect TIME_WAIT to old duplicate segment absorption and final ACK retransmission.
- priority: medium

### WP-12 Retry and Idempotency

- topic: Distributed Systems
- symptom: Proposes retry without duplicate prevention.
- evidence: No idempotency key, unique constraint, dedupe table, or retry budget.
- root_cause: Reliability mechanism considered without side effects.
- drill: Design order-create API under client timeout and retry.
- success_criteria: Can explain request id, unique index, state machine, and safe retry response.
- priority: high

### WP-13 Message Queue Semantics

- topic: Distributed Systems
- symptom: Says "use MQ to decouple" without discussing delivery semantics.
- evidence: No retry, DLQ, ordering, dedupe, or poison message plan.
- root_cause: Architecture pattern used as magic box.
- drill: Walk through consumer crash after DB write before ACK.
- success_criteria: Can explain at-least-once delivery and consumer idempotency.
- priority: high

### WP-14 System Design Capacity

- topic: System Design
- symptom: Starts design before estimating traffic, storage, and hot paths.
- evidence: No QPS, read/write ratio, p95 latency target, or data size.
- root_cause: Solution-first instead of constraint-first thinking.
- drill: For any design problem, write assumptions before components.
- success_criteria: Can produce capacity assumptions and tie them to DB/cache/MQ choices.
- priority: medium

### WP-15 Project Story Evidence

- topic: Behavioral + Project
- symptom: Project answers are generic and lack personal ownership.
- evidence: Uses "we optimized" without saying exact role, metric, or decision.
- root_cause: Experience not converted into interview-ready stories.
- drill: Write STAR cards for two projects with metric and tradeoff.
- success_criteria: Can say "I changed X, measured Y, learned Z" in under 90 seconds.
- priority: high

### WP-16 LearnForge Architecture Explanation

- topic: AI Agent Project
- symptom: Describes LearnForge as "multi-agent learning app" but misses invariants.
- evidence: No mention of Manager as sole orchestrator/writer, read-only Diagnosis, or Retrieval as shared capability.
- root_cause: Architecture not compressed into interview answer.
- drill: Prepare 60s, 2min, and 5min versions of LearnForge.
- success_criteria: Can explain why hierarchical agents were chosen over swarm-style collaboration.
- priority: high

## Diagnosis Clusters

### Cluster A: Mechanism Without Boundary

- points: WP-01, WP-04, WP-06, WP-08
- pattern: Candidate knows what a component does but not what it does not guarantee.
- repair: For every concept, add "guarantees / does not guarantee / failure mode".

### Cluster B: Pattern Without Production Cost

- points: WP-02, WP-12, WP-13, WP-14
- pattern: Candidate proposes common architecture pattern without cost or operational behavior.
- repair: Attach metrics, retry budget, monitoring, and rollback plan.

### Cluster C: Terms Mixed by Traffic Shape

- points: WP-03, WP-09, WP-11
- pattern: Similar concepts are memorized as vocabulary and confused under examples.
- repair: Classify by trigger, timeline, and observable symptom.

### Cluster D: Project Story Not Interview-Ready

- points: WP-15, WP-16
- pattern: Real work exists but is not packaged with ownership and evidence.
- repair: Convert projects into 60s answer cards with tradeoffs.

## Practice Queue

1. Redis consistency timeline.
2. ThreadPoolExecutor saturation trace.
3. MVCC visibility examples under RC and RR.
4. Idempotent order creation design.
5. LearnForge architecture pitch.
6. Cache failure mode classification.
7. B+ tree page/fanout explanation.
8. GC production diagnosis playbook.
9. MQ at-least-once consumer crash timeline.
10. Project STAR answer with metric.

