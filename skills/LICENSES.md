# Skills installed by the AI Software Factory

Copied unchanged (minus authoring test logs and one TypeScript example file) into
every product's `.claude/skills/` so that whoever works on the product in Claude
Code keeps the same code discipline the factory's agents are held to (the
"Code discipline" section of `PROJECT_RULES.md`).

| Skill | What it prevents | Source | License |
|---|---|---|---|
| ponytail | code that need not exist: YAGNI ladder, reuse before writing, shortest working diff | ponytail 4.8.3 (DietrichGebert) | MIT, `licenses/ponytail.LICENSE` |
| ponytail-review | over-engineering in review: unrequested abstractions, dead options | ponytail 4.8.3 | MIT, `licenses/ponytail.LICENSE` |
| karpathy-guidelines | silent assumptions, overcomplication, drive-by edits, unverifiable goals | andrej-karpathy-skills 1.0.0 | MIT (skill frontmatter) |
| test-driven-development | code without a failing test first | superpowers 6.4.1 (Jesse Vincent) | MIT, `licenses/superpowers.LICENSE` |
| systematic-debugging | symptom patches instead of root-cause fixes | superpowers 6.4.1 | MIT, `licenses/superpowers.LICENSE` |
| verification-before-completion | "done" claims without evidence | superpowers 6.4.1 | MIT, `licenses/superpowers.LICENSE` |
