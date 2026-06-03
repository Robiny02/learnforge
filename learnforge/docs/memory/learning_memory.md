# Learning Memory Structure Pack

## Purpose

This file documents how learning memory should be written in a structured way. It complements the runtime files under `data/memory/`.

## Memory Layers

| Layer | File or Store | Content | Write Policy |
|---|---|---|---|
| Stable rule memory | `data/memory/MEMORY.md` | Long-term preferences, goals, hard rules | Only explicit "remember" requests |
| Daily memory | `data/memory/YYYY-MM-DD.md` | QA, mock feedback, weak points, progress, decisions | Automatically when useful |
| Session memory | `data/session_memory/session_state_*` | Rolling summary and recent turns | Per conversation |
| Derived index | SQLite `chunks` with local scope | Searchable memory chunks | Rebuildable from md files |

## Daily Note Format

```text
## <iso8601> · <kind> · <topic>
importance: <0.00-1.00>
<structured content>
```

## Supported Kinds

| Kind | Meaning | Importance | Example |
|---|---|---:|---|
| `qa` | Useful answer summary | 0.50 | B+ tree range query explanation |
| `weak` | Diagnosed weak point | 0.90 | MVCC visibility unclear |
| `mock` | Mock interview result | 0.80 | Redis round scored low depth |
| `progress` | Learning progress | 0.80 | Completed cache consistency drill |
| `decision` | Project or learning decision | 1.00 | Prioritize Redis and concurrency |
| `note` | General note | 0.40 | Miscellaneous observation |

## Write Criteria

### Write as `weak`

- User answers incorrectly in mock.
- User repeatedly asks the same concept.
- Judge flags vague, no_evidence, or overclaim.
- Diagnosis finds low mastery plus recent weak signals.
- User admits confusion about a topic.

### Write as `mock`

- A mock session settles.
- A round reveals a notable answer pattern.
- A follow-up exposes a deeper weakness.
- A session is escalated into diagnosis or planning.
- Coach produces next steps worth remembering.

### Write as `progress`

- User completes a drill.
- User improves a previously weak explanation.
- User creates a reusable project story.
- User finishes a daily plan item.
- User validates a concept through tests or implementation.

### Write as `decision`

- User changes interview target or deadline.
- Manager modifies learning path.
- User chooses priority topics.
- Architecture/design choice is made for the project.
- A testing or evaluation strategy is selected.

## Structured Content Templates

### Weak Point Template

```text
weak_point:
- topic:
- symptom:
- evidence:
- root_cause:
- repair_drill:
- next_review:
```

### Mock Session Template

```text
mock_session:
- topic:
- turns:
- strongest_answer:
- weakest_answer:
- score_pattern:
- risk_flags:
- next_questions:
```

### Progress Template

```text
progress:
- topic:
- completed:
- new_capability:
- remaining_gap:
- next_step:
```

### Decision Template

```text
decision:
- context:
- choice:
- reason:
- expected_effect:
- revisit_condition:
```

## Retrieval Hints

- Put the topic in the heading when possible, such as `weak · Redis`.
- Include exact keywords the user may later search: Redis persistence, cache penetration, MVCC, thread pool.
- Keep one memory note focused on one concept or event.
- Prefer evidence and next action over long explanation.
- Do not store raw secrets, private credentials, or long logs.

## Suggested Expansion Points

1. Convert each weak point in `weak_points.md` into individual `weak` notes after actual evidence appears.
2. Convert each mock answer card into `mock` notes with score dimensions.
3. Add a weekly summary file generated from daily memory.
4. Add `review_due` metadata for weak points that need spaced repetition.
5. Add `source_session_id` for mock-derived weak points.
6. Add `evidence_turn` for exact interview round references.
7. Add a cleanup script that deduplicates repeated stub QA notes.
8. Add a memory dashboard grouping notes by topic and kind.
9. Add a recall evaluation set for memory retrieval quality.
10. Add a promotion rule: repeated `weak` notes can become a stable learning goal in `MEMORY.md`.

