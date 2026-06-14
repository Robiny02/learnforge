# Vendored interview rubrics — attribution

The deterministic rubrics encoded in `learnforge/agents/mock/interview_skill.py`
(role-type detection, evidence/claim levels, truth-boundary downgrades,
interview grilling rounds, answer-card structure) are **derived and distilled**
from the open-source Codex/Claude Skill:

- **LLMInternSkill** — <https://github.com/couragec/llm-intern-skill>
- License: MIT

Only the *rubric logic* (keyword maps, claim levels, grilling rounds, answer-card
format) was adapted into Python so LearnForge's mock interview can run it offline.
We did **not** vendor the skill's resume/LaTeX/project-scout assets — those are
outside the mock-interview scope. The original prose lives in the upstream repo's
`SKILL.md` and `skill-references/` (interview-grilling, evidence-contract,
truth-boundary, answer-cards, jd-analysis, roles/).

Core principle carried over verbatim in spirit:

> Do not fabricate. Diagnose first, polish second.
> Every strong claim needs evidence; flag overclaim instead of rewarding it.

---

## tech-interview-skill — interviewer persona & question bank

The interviewer **persona** baked into `INTERVIEWER_SKILL` (direct/no-flattery grilling,
resume-integrity callouts, dual-role 面试官↔导师 hints with `---`/💡, three-phase
基础→项目→系统设计 flow, scoring & report rubric) and the high-frequency **question bank**
in `interview_skill.QUESTION_PATTERNS` are derived from the open-source Claude Skill:

- **tech-interview-skill** — <https://github.com/Lntanohuang/tech-interview-skill>
  (`tech-interview/SKILL.md`, `interview-coach/SKILL.md`,
  `tech-interview/references/question-patterns.md`)

The reference question patterns (themselves credited upstream to CS-Notes / JavaGuide /
小林coding / fe-interview / advanced-java) are vendored for offline reference in
`question-patterns.md` and distilled into Python data for offline fallback question
generation. Only the interviewer rubric/persona and the question stems were adapted;
the skill's resume-polish / WebSearch resource-linking flows were not vendored.
