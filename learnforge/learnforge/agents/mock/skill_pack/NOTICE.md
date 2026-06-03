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
