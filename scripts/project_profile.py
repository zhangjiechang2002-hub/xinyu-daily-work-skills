"""Project-specific report rules, kept separate from the shared AF/Feishu workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"
REQUIRED_QUALITY_FIELDS = (
    "roi_green", "payer_cost_green_below", "payer_cost_code_below",
    "cpi_green_below", "cpi_code_below",
)


def load_profile(config: dict[str, Any], config_path: Path | None = None) -> dict[str, Any] | None:
    requested = config.get("project_profile")
    raw_key = str(config.get("game_prefix") or config.get("game") or "").strip()
    key = raw_key.upper()
    if requested:
        path = Path(requested)
        if not path.is_absolute() and config_path is not None:
            path = config_path.parent / path
    else:
        path = None
        for candidate in PROFILE_DIR.glob("*.json"):
            if candidate.name.endswith(".example.json"):
                continue
            candidate_profile = json.loads(candidate.read_text(encoding="utf-8"))
            names = [candidate_profile.get("project_key", ""), *candidate_profile.get("aliases", [])]
            if key and any(key == str(name).strip().upper() for name in names):
                path = candidate
                break
        if path is None:
            return None
    profile = json.loads(path.read_text(encoding="utf-8"))
    validate_profile(profile)
    accepted = [profile["project_key"], *profile.get("aliases", [])]
    if key and not any(key == str(name).strip().upper() for name in accepted):
        raise ValueError(f"Project profile {profile['project_key']} does not match game_prefix {key}")
    # Legacy monthly workflows may use a known project label but target another workbook.
    for name in ("wiki_token", "spreadsheet_token"):
        if not requested and config.get(name) and config[name] != profile["destination"].get(name):
            return None
    return profile


def validate_profile(profile: dict[str, Any]) -> None:
    key = profile.get("project_key")
    if not isinstance(key, str) or not key or key == "REPLACE_WITH_PROJECT_KEY":
        raise ValueError("Set a real project_key in the project profile")
    aliases = profile.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
        raise ValueError(f"{key}: aliases must be a list of non-empty names")
    workspace = str(profile.get("shared_workspace") or "")
    if not workspace or workspace.startswith("REPLACE_WITH_"):
        raise ValueError(f"{key}: set shared_workspace to the Feishu adapter folder")
    workspace = profile.get("shared_workspace")
    if workspace is not None and not isinstance(workspace, str):
        raise ValueError(f"{key}: shared_workspace must be a path string")
    destination = profile.get("destination", {})
    if not any(destination.get(name) for name in ("wiki_token", "spreadsheet_token")):
        raise ValueError(f"{key}: destination wiki_token or spreadsheet_token is required")
    if any(str(value).startswith("REPLACE_WITH_") for value in destination.values()):
        raise ValueError(f"{key}: replace destination placeholders before running")
    if not (destination.get("template_sheet_id") or destination.get("template_source") == "first_sheet"):
        raise ValueError(f"{key}: template_sheet_id or first_sheet template source is required")
    for platform in ("GP", "IOS"):
        rules = profile.get("quality", {}).get(platform)
        if not isinstance(rules, dict) or any(name not in rules for name in REQUIRED_QUALITY_FIELDS):
            raise ValueError(f"{key}: complete quality rules required for {platform}; use null for unused metrics")
        roi = rules["roi_green"]
        roi_blue = rules.get("roi_blue")
        primary = rules.get("primary_metric", "首日ROI")
        if not isinstance(roi, dict) or primary not in roi:
            raise ValueError(f"{key}: {platform} needs its primary_metric in roi_green; use null if not assessed")
        if roi_blue is not None and not isinstance(roi_blue, dict):
            raise ValueError(f"{key}: {platform} roi_blue must be an object when configured")
        if not isinstance(rules.get("payer_cost_headers", ["单次付费人数费用", "总付费人数成本"]), list):
            raise ValueError(f"{key}: {platform} payer_cost_headers must be a list")
        for field, value in {**roi, **{name: rules[name] for name in REQUIRED_QUALITY_FIELDS[1:]}}.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"{key}: invalid {platform} threshold for {field}")
        for field, value in (roi_blue or {}).items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"{key}: invalid {platform} blue threshold for {field}")
    historical = profile.get("excellent", {}).get("historical")
    if historical is not None:
        if historical.get("keep") != "highest_spend" or historical.get("dedupe_fields") != ["渠道", "编号"]:
            raise ValueError(f"{key}: unsupported historical dedupe strategy")
        if historical.get("refresh_script"):
            allowed_refresh = {"weekly", "monthly"}
            if not set(historical.get("refresh_on", [])).issubset(allowed_refresh) or not historical.get("refresh_on"):
                raise ValueError(f"{key}: historical refresh_on must contain weekly and/or monthly")
            if not set(historical.get("source_sheet_types", [])).issubset(allowed_refresh) or not historical.get("source_sheet_types"):
                raise ValueError(f"{key}: historical source_sheet_types must contain weekly and/or monthly")
            if not isinstance(historical.get("source_year"), int) or not isinstance(historical.get("first_source_month"), int):
                raise ValueError(f"{key}: historical refresh needs source_year and first_source_month")
            if historical.get("spend_gt") is None or not historical.get("spreadsheet_token") or not historical.get("template_sheet_id"):
                raise ValueError(f"{key}: historical refresh needs spend and destination template")
    for header, values in profile.get("dropdowns", {}).items():
        if not isinstance(header, str) or not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
            raise ValueError(f"{key}: invalid dropdown options for {header}")


def validate_active_quality(profile: dict[str, Any], platform: str) -> None:
    rules = profile["quality"][platform]
    values = [*rules["roi_green"].values(), *(rules[name] for name in REQUIRED_QUALITY_FIELDS[1:])]
    if all(value is None for value in values):
        raise ValueError(f"{profile['project_key']}: no quality thresholds configured for {platform}")


def apply_profile(config: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    if profile is None:
        return config
    config.setdefault("game_prefix", profile["project_key"])
    for name, value in profile["destination"].items():
        if name in config and config[name] != value:
            raise ValueError(f"{profile['project_key']}: {name} conflicts with project profile")
        config.setdefault(name, value)
    for name in ("infer_channel_from_ad", "template_dropdowns", "freshness_from_ad_date"):
        if name in profile:
            config.setdefault(name, profile[name])
    config["_project_profile"] = profile
    return config


def code_color(row: dict[str, Any], rules: dict[str, Any]) -> str | None:
    def numeric(value):
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(",", "").replace("%", "")) / (100 if "%" in str(value) else 1)
        except ValueError:
            return None

    roi = rules["roi_green"]
    primary = rules.get("primary_metric", "首日ROI")
    d0 = numeric(row.get(primary))
    d0_limit = roi.get(primary)
    if d0_limit is not None and d0 is not None and d0 > d0_limit:
        return "green"
    blue_roi = rules.get("roi_blue")
    if blue_roi is None:
        blue_roi = {name: limit for name, limit in roi.items() if name != primary}
    if any(numeric(row.get(name)) is not None and numeric(row.get(name)) > limit
           for name, limit in blue_roi.items() if name != primary and limit is not None):
        return "blue"
    payer = next((value for name in rules.get("payer_cost_headers", ["单次付费人数费用", "总付费人数成本"])
                  if (value := numeric(row.get(name))) is not None), None)
    payer_limit = rules["payer_cost_code_below"]
    if payer_limit is not None and payer is not None and payer < payer_limit:
        return "yellow"
    cpi = numeric(row.get(rules.get("cpi_header", "CPI")))
    cpi_limit = rules["cpi_code_below"]
    if str(row.get("模式") or "").upper() != "MAI" and cpi_limit is not None and cpi is not None and cpi < cpi_limit:
        return "red"
    return None

