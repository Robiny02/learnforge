"""按 gpt-image-2-style-library 风格库的五段式拼 prompt，生成两张架构图到 docs/assets/。

直接运行：python3.11 scripts/gen_architecture_diagrams.py
（出图模型经 OpenRouter，key 取自 .env 的 OPENROUTER_API_KEY）
"""

from __future__ import annotations

from mcp_gpt_image import generate_image_core  # 同目录

# house style 公共前缀（对齐 SKILL.md + references/palette.md, layout.md）
STYLE = (
    "A clean flat vector technical architecture infographic, off-white background (#F7F9FC), "
    "rounded-rectangle nodes with thin slate borders (#CBD5E1) and very subtle shadows, "
    "sans-serif labels, monospace font for class/tool names, horizontal swimlanes for layers, "
    "directed labeled arrows with orthogonal routing and few crossings, numbered call sequence "
    "in small circles, a small legend box at bottom-right. "
    "Color system: indigo #4F46E5 for the main orchestration path, teal #0EA5A4 for read-only/"
    "external tools, amber #F59E0B for decision/permission gates, slate #1E293B for text, "
    "gray #64748B for muted labels. Use color groups, icons and clean spacing to reduce clutter. "
    "Short labels only, correct English spelling, no long paragraphs inside the image. "
    "Professional, minimal, high readability. "
)

DIAG1 = STYLE + (
    "TITLE at top center: 'Reactor-agent — Strategy-based Agent Runtime'. "
    "Define 4 module groups as stacked swimlanes:\n"
    "(1) 'Dispatch' lane: a node 'AgentDispatchService' -> an amber diamond decision 'agentType?' "
    "-> three strategy nodes 'ReAct (default)', 'Plan-Solve', 'Flow'. Mark 'ReAct (default)' as the "
    "fallback with a small 'default' tag.\n"
    "(2) 'Strategy (IExecuteStrategy)' lane: the three strategies map down to a domain runtime "
    "'armory StrategyHandler (think-act-observe loop)'.\n"
    "(3) 'Skill — 3-level progressive disclosure' lane: 'L1 name+description (always in prompt)' "
    "-> 'L2 SKILL.md body (skill.load on demand)' -> 'L3 references/scripts (Read/Glob/Grep via "
    "SkillPathGuard)'. Show these as three teal nodes left-to-right.\n"
    "(4) 'MCP Registry' lane: a node 'Unified MCP Registry' connected to three teal tool nodes "
    "'SSE', 'STDIO', 'Streamable HTTP'.\n"
    "Arrows: strategy runtime --uses--> Skill lane; Skill/runtime --tool_use--> MCP Registry. "
    "Legend: indigo=main path, teal=read-only tools, amber=decision."
)

DIAG2 = STYLE + (
    "TITLE at top center: 'LearnForge ResearchAgent — tool-calling ReAct to Report'. "
    "Define 5 modules:\n"
    "(1) Top: 'Manager (orchestrator)' --dispatch agent.research--> the center agent.\n"
    "(2) Center indigo node 'ResearchAgent' with a small circular ReAct loop labeled "
    "'① think -> ② tool_use -> ③ observe'.\n"
    "(3) A teal cluster of read-only expert tools the agent calls: 'github.repo_read', "
    "'context7.docs', 'fetch.url', 'retrieval.search', 'repository.read', 'skill.load / read_file'. "
    "Bidirectional arrows 'tool_use / tool_result' between ResearchAgent and this cluster.\n"
    "(4) 'report.generate (Markdown)' node, then 'Summary' node, producing a document "
    "'ResearchOutput: latest tech points / architecture / LearnForge takeaways' with steps ④ report ⑤ summary.\n"
    "(5) A rose (#F43F5E) DASHED boundary box around Manager's write path labeled 'Manager = sole writer', "
    "drawn clearly OUTSIDE/separate from ResearchAgent to show ResearchAgent is strictly read-only. "
    "Legend: indigo=agent main loop, teal=read-only tools, rose dashed=write boundary (off-limits)."
)


def main() -> None:
    for fname, prompt in [
        ("reactor-agent-architecture.png", DIAG1),
        ("learnforge-research-report-chain.png", DIAG2),
    ]:
        print(f">>> generating {fname} ...")
        print(generate_image_core(prompt, filename=fname))


if __name__ == "__main__":
    main()
