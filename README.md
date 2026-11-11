# AF Feishu Weekly Game Skill

A reusable Codex skill for turning AppsFlyer CSV exports into project-specific Feishu weekly/monthly performance sheets.

## Install

Copy this folder to:

```text
~/.codex/skills/af-feishu-weekly-game
```

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

The runtime also needs the local Feishu adapter `sync_af_export_to_feishu.py` and its dependencies. Point each private project profile at that adapter's folder with `shared_workspace`.

## Project profiles

Do not commit real credentials or private destinations. Copy `profiles/new-project.example.json` to a private location, complete the onboarding questions, and reference that profile from the task config.

Every new project is intentionally blocked until it has an independent profile. Once validated, that profile becomes the project's default analysis template.

## Credentials

Set these only in the local environment:

```powershell
$env:FEISHU_APP_ID="your-app-id"
$env:FEISHU_APP_SECRET="your-app-secret"
```

Run secret scanning before publishing the repository.

