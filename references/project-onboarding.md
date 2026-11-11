# New project onboarding

Ask the user for the following information before creating a profile or writing data. Combine the questions into a compact request, but do not omit any applicable category.

## Identity and destination

- Project name, stable project key, and accepted aliases.
- GP, iOS, or both.
- Feishu wiki or spreadsheet destination.
- Template sheet name/ID and whether the first sheet is always the template.
- Project workspace containing the Feishu adapter module.

## Input and field meanings

- Representative AF CSV for each platform/channel.
- Campaign/Ad rules for channel, mode, material type, material direction, material code, and new/old material.
- Exact source and template headers for spend, revenue, D0/D3/D7/D14/D30/later ROI, installs, registrations, payers, impressions, and clicks.
- Aggregation and dedupe keys, including whether Campaign and Ad must both match.
- Dropdown option values and text alignment requirements.

## Platform assessment

Collect GP and iOS separately:

- Primary quality metric.
- ROI thresholds for every assessed period.
- Payer-cost, registration-cost, and CPI thresholds.
- Whether each threshold colors the metric cell, the code cell, or both.
- Code-color priority and any exclusions such as MAI.
- Minimum spend/sample size required before judging a material.

Use `null` for a metric the project does not assess. Do not guess it.

## Outputs

- Weekly/monthly sheet naming and ordering.
- Whether right-side material-direction analysis is required, plus its sample-size standard.
- Designer-facing excellent-material destination and spend floor.
- Historical excellent destination, source months, dedupe key, and winning-row rule.
- Whether weekly writes, monthly writes, or both automatically refresh historical summaries; which source sheet types feed the summaries; and whether a one-platform update rebuilds both GP and iOS summaries.

## Confirmation and acceptance

Summarize the proposed profile back to the user before the first external write. Then test a small known sample and verify:

- GP/iOS rows go to the correct sheets.
- Boundary values do not qualify when the rule is strict `>` or `<`.
- Code-color priority is correct.
- Dropdowns and formats match the template.
- Spend totals, row counts, dedupe behavior, and cost-desc order survive read-back.
- No existing project profile or historical sheet changed.

Mark the profile validated only after these checks pass. Later requests in the same project use the profile automatically without repeating onboarding.

