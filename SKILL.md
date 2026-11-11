---
name: af-feishu-weekly-game
description: Create or update project-specific Feishu game performance sheets from AppsFlyer CSV exports. Use for GP/iOS weekly or monthly reports, template copying, metric recomputation, quality highlighting, material analysis, and excellent-material summaries. Every new project must be onboarded into an independent validated profile before any write.
---

# AF Feishu Weekly Game

Process AppsFlyer CSV exports into existing Feishu spreadsheet templates. Keep the data workflow shared while isolating every project's destination, template, fields, thresholds, highlights, analysis, and excellent-material rules in its own profile.

## New project gate

If the requested project has no validated JSON file in `profiles/`, stop before writing to Feishu and read [references/project-onboarding.md](references/project-onboarding.md).

Ask the user the onboarding questions. Do not infer missing thresholds from another game, from a template color, or from general industry practice. Confirm the answers, create `profiles/<project-key>.json`, validate it, and run a small GP/iOS acceptance sample before treating the profile as active.

This gate is required once for every new project. After validation, the project profile is its fixed default analysis template. In that project's chat, the user may simply attach CSV files and request a weekly or monthly update; do not ask them to repeat the template or thresholds.

## Isolation rules

- Never copy destinations, template IDs, dropdown values, field aliases, thresholds, color priority, spend floors, or historical-summary rules across projects.
- Select a profile only by the explicit project identity or an alias stored in that profile. Never select by metric similarity.
- Reject an unbound project or a platform with no active quality threshold.
- Keep credentials in environment variables. Never store `FEISHU_APP_ID` or `FEISHU_APP_SECRET` in the skill, a profile, logs, or Git.
- Feishu spreadsheet/wiki tokens and template sheet IDs belong in local project profiles. Treat profiles containing real destinations as private unless the repository itself is private and the user explicitly accepts that exposure.

## Shared workflow

1. Load the project's validated profile and the target Feishu template.
2. Read headers and dropdown validations dynamically; do not assume old column letters.
3. Normalize AF fields, filter numeric zero-spend rows, remove fully blank rows, and deduplicate by the configured business key.
4. Aggregate only when the project's rules require it. Recompute costs and ratios from summed numerators and denominators; never average ROI, CPI, CTR, CVR, or payer cost directly.
5. Sort the main table by spend descending and preserve template styles, validation, alignments, number formats, and row dimensions.
6. Apply metric-cell and code-column colors from the active platform rules, respecting configured priority.
7. Build material-direction analysis only when `analysis_enabled` is true and the profile supplies compatible ROI and cost meanings.
8. Refresh designer or historical excellent summaries only when that project explicitly configures the workflow. A project may configure weekly writes, monthly writes, or both as triggers and source sheet types; a one-platform source update may still require rebuilding both GP and iOS summaries.
9. Read the written sheet back and verify row count, business keys, spend total, spend order, formats, validation, and summary refresh status.

## Runtime

Use `scripts/run_weekly_game_report.py` with a task JSON that names the project and its profile. The runner expects a workspace module named `sync_af_export_to_feishu.py`; provide its folder through `--workspace` or `shared_workspace` in the private project profile.

```powershell
$env:FEISHU_APP_ID="..."
$env:FEISHU_APP_SECRET="..."
python scripts/run_weekly_game_report.py --config C:\path\to\task.json
```

Start a new profile from `profiles/new-project.example.json`. `null` means a metric is intentionally unused; it must never mean "reuse another project's value".

## Completion report

For each written sheet, report its title, source date range, input/written/dropped row counts, duplicate count, spend-sort verification, analysis status, historical-refresh status, and any skipped or abnormal records. Never print credentials.

