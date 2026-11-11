import argparse
import json
import math
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_profile import apply_profile, code_color, load_profile, validate_active_quality


AILIN2_WIKI_TOKEN = ""
AILIN2_TEMPLATE_SHEET_ID = ""
SHENYU_TURKEY_WIKI_TOKEN = ""
SHENYU_TURKEY_TEMPLATE_SHEET_ID = ""
SHENYU_TURKEY_BASE_SHEET_ID = ""
AILIN2_DATA_ROI_THRESHOLDS = {
    "首日ROI": 0.09,
    "3日ROI": 0.28,
    "7日ROI": 0.48,
    "14日ROI": 0.73,
    "后续ROI": 1.45,
}
AILIN2_CODE_CPI_THRESHOLDS = {"GP": 3, "IOS": 10}
AILIN2_PAYER_COST_THRESHOLDS = {"GP": 30, "IOS": 60}
AILIN2_CODE_LATE_BLUE = "#9fc5e8"
PAYER_COST_HEADERS = ("单次付费人数费用", "总付费人数成本")
SHENYU_TURKEY_D0_ROI_THRESHOLDS = {"GP": 0.07, "IOS": 0.07}
SHENYU_TURKEY_D3_ROI_THRESHOLDS = {"GP": 0.17, "IOS": 0.17}
SHENYU_TURKEY_D7_ROI_THRESHOLDS = {"GP": 0.35, "IOS": 0.35}
SHENYU_TURKEY_CPI_THRESHOLDS = {"GP": 23, "IOS": 25}
SHENYU_TURKEY_DATA_PAYER_COST_THRESHOLDS = {"GP": 90, "IOS": 125}
SHENYU_TURKEY_CODE_PAYER_COST_THRESHOLDS = {"GP": 130, "IOS": 150}
SHENYU_TURKEY_CODE_LATE_D0_MAX = 0.07
SHENYU_TURKEY_CODE_LATE_BLUE = "#9fc5e8"
AILIN2_NOTE_TEMPLATE = (
    "数据拉取时间：{pull_time}；爱琳项目标注说明：数据指标单元格绿色=优质数据"
    "（首日ROI>9%、3日ROI>28%、7日ROI>48%、14日ROI>73%、后续ROI>145%、单次付费人数费用GP<30/iOS<60、IOS CPI<10）；"
    "编号列绿色=首日优秀素材（首日ROI>9%），天蓝色=后续好素材（首日ROI<=9%且3日ROI>28%、7日ROI>48%、14日ROI>73%、后续ROI>145%任一达标），"
    "黄色=付费成本低素材（单次付费人数费用GP<30/iOS<60），红色=注册单价低素材（CPI低于平台阈值且模式非MAI）；编号列优先级：绿色>天蓝色>黄色>红色；MAI仅因CPI低不标红。"
)
SHENYU_WEEKLY_HEADERS = [
    "渠道",
    "模式",
    "素材方向",
    "编号",
    "Ad",
    "cost",
    "首充",
    "首日ROI",
    "3日ROI",
    "7日ROI",
    "14日ROI",
    "30日ROI",
    "后续ROI",
    "累计收入",
    "付费人数",
    "单次付费人数费用",
    "D0付费次数",
    "单次付费次数费用",
    "安装",
    "CPI",
    "CTR",
    "CVR",
    "CPC（点击成本）",
    "CPM",
    "付费率",
    "展示",
    "点击",
    "注册",
    "单次注册人数费用",
]


PERCENT_HEADERS = {"首日ROI", "3日ROI", "7日ROI", "14日ROI", "30日ROI", "后续ROI", "CTR", "CVR", "付费率", "次日留存", "3日留存"}
MONEY_HEADERS = {
    "cost",
    "首充",
    "累计收入",
    "单次付费人数费用",
    "总付费人数成本",
    "单次付费次数费用",
    "总付费次数成本",
    "总付费次数费用",
    "D0付费次数成本",
    "CPI",
    "CPC（点击成本）",
    "CPM",
    "单次注册人数费用",
    "注册成本",
}
INTEGER_HEADERS = {"付费人数", "总付费人数", "总付费次数", "D0付费次数", "安装", "展示", "点击", "注册"}


def load_sync_module(workspace: Path):
    sys.path.insert(0, str(workspace))
    import sync_af_export_to_feishu as sync  # type: ignore

    for name in ("query_sheets", "read_sheet_values", "update_values", "set_styles"):
        original = getattr(sync, name, None)
        if not callable(original):
            continue

        def make_retry(func):
            def wrapped(*args, **kwargs):
                last_error = None
                for attempt in range(6):
                    try:
                        return func(*args, **kwargs)
                    except requests.RequestException as exc:
                        last_error = exc
                        time.sleep(3 + attempt * 2)
                raise last_error

            return wrapped

        setattr(sync, name, make_retry(original))

    return sync


def apply_destination_defaults(config: dict[str, Any]) -> dict[str, Any]:
    return config


def profile_quality_cell_ranges(sync, rows, target_headers, sheet_id, platform, profile):
    rules = profile["quality"][platform]
    ranges = []
    for row_num, values in rows:
        row = dict(zip(target_headers, values))
        for name, threshold in rules["roi_green"].items():
            if threshold is not None and name in row:
                value = as_num(row[name])
                if value is not None and value > threshold:
                    col = sync.column_letter(target_headers.index(name) + 1)
                    ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
        payer_name = first_existing_header(target_headers, tuple(rules.get("payer_cost_headers", PAYER_COST_HEADERS)))
        payer_limit = rules["payer_cost_green_below"]
        if payer_name and payer_limit is not None:
            value = as_num(row[payer_name])
            if value is not None and value < payer_limit:
                col = sync.column_letter(target_headers.index(payer_name) + 1)
                ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
        cpi_limit = rules["cpi_green_below"]
        cpi_name = rules.get("cpi_header", "CPI")
        if cpi_name in row and cpi_limit is not None:
            value = as_num(row[cpi_name])
            if value is not None and value < cpi_limit:
                col = sync.column_letter(target_headers.index(cpi_name) + 1)
                ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
    return ranges


def apply_profile_code_highlight(sync, headers, spreadsheet_token, sheet_id, rows, target_headers, platform, profile):
    if not rows or "编号" not in target_headers:
        return
    col = sync.column_letter(target_headers.index("编号") + 1)
    groups = {name: [] for name in ("green", "blue", "yellow", "red")}
    all_rows = []
    for row_num, values in rows:
        row = dict(zip(target_headers, values))
        if row.get("编号") in (None, ""):
            continue
        all_rows.append(row_num)
        color = code_color(row, profile["quality"][platform])
        if color:
            groups[color].append(row_num)
    styles = []
    white_ranges = sync.contiguous_ranges(all_rows, sheet_id, col, col)
    if white_ranges:
        styles.append({"ranges": white_ranges, "style": {"font": {"bold": False, "clean": False}, "backColor": sync.DATA_WHITE, "foreColor": sync.FONT_BLACK, "clean": False}})
    for name, background in (("green", sync.QUALITY_GREEN), ("blue", "#9fc5e8"), ("yellow", sync.QUALITY_YELLOW), ("red", sync.QUALITY_RED)):
        ranges = sync.contiguous_ranges(groups[name], sheet_id, col, col)
        if ranges:
            styles.append({"ranges": ranges, "style": {"font": {"bold": False, "clean": False}, "backColor": background, "foreColor": sync.FONT_BLACK, "clean": False}})
    if styles:
        sync.set_styles(headers, spreadsheet_token, styles)


def profile_pull_note(profile: dict[str, Any], pull_time: str, platform: str) -> str:
    note_template = profile.get("note_template")
    if note_template:
        return str(note_template).format(
            pull_time=pull_time,
            platform=platform,
            project_key=profile["project_key"],
        )
    rules = profile["quality"][platform]
    roi_parts = [f"{name}>{limit:.0%}" for name, limit in rules["roi_green"].items() if limit is not None]
    cost = rules["payer_cost_code_below"]
    cpi = rules["cpi_code_below"]
    parts = [*roi_parts]
    if cost is not None:
        parts.append(f"付费人数成本<{cost:g}")
    if cpi is not None:
        parts.append(f"CPI<{cpi:g}（非MAI）")
    detail = "、".join(parts) if parts else "本项目尚未设置质量阈值"
    return f"数据拉取时间：{pull_time}；{profile['project_key']} {platform} 标注规则：{detail}；编号优先级：首日好>后续好>低付费成本>低CPI。"


def normalize_profile_dropdown_values(rows: list[dict[str, Any]], profile: dict[str, Any]) -> None:
    """Normalize generated values to each profile's canonical dropdown labels."""
    dropdowns = profile.get("dropdowns", {})
    for row in rows:
        for header, options in dropdowns.items():
            value = row.get(header)
            if value in (None, ""):
                continue
            text = str(value).strip()
            canonical = next((option for option in options if option.casefold() == text.casefold()), None)
            if canonical is None and header == "新素材" and text == "老素材" and "旧素材" in options:
                canonical = "旧素材"
            if canonical is not None:
                row[header] = canonical


def apply_profile_dropdowns(sync, headers, spreadsheet_token, sheet_id, target_headers, last_row, profile):
    if last_row < 3:
        return
    palette = ["#bacefd", "#fed4a4", "#d9ead3", "#fce5cd", "#b1e8fc", "#ead1dc"]
    for name, options in profile.get("dropdowns", {}).items():
        if name not in target_headers:
            raise ValueError(f"{profile['project_key']}: dropdown column missing from template: {name}")
        col = sync.column_letter(target_headers.index(name) + 1)
        payload = {
            "range": f"{sheet_id}!{col}3:{col}{last_row}",
            "dataValidationType": "list",
            "dataValidation": {
                "conditionValues": options,
                "options": {
                    "highlightValidData": True,
                    "multipleValues": False,
                    "colors": [palette[index % len(palette)] for index in range(len(options))],
                },
            },
        }
        api("POST", f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dataValidation", headers={**headers, "Content-Type": "application/json"}, json=payload)


def resolve_destination(sync, headers, config: dict[str, Any]) -> str:
    if config.get("spreadsheet_token"):
        return str(config["spreadsheet_token"])
    wiki_token = config.get("wiki_token")
    if not wiki_token:
        return sync.resolve_spreadsheet_token(headers)
    response = requests.get(
        "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node",
        params={"token": wiki_token},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Wiki destination resolve error: {payload}")
    node = payload["data"]["node"]
    if node.get("obj_type") != "sheet":
        raise RuntimeError(f"Wiki destination is not a sheet: {node.get('obj_type')}")
    return node["obj_token"]


def as_num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) else float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or text.upper() == "N/A":
        return None
    has_percent = "%" in text
    text = text.replace("%", "")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed / 100 if has_percent else parsed


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def inferred_roi_from_cost(rows: list[dict[str, Any]], roi_field: str) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for row in rows:
        cost = as_num(row.get("cost"))
        if cost is None or cost <= 0:
            continue
        denominator += cost
        value = as_num(row.get(roi_field))
        if value is not None:
            numerator += cost * value
    return numerator / denominator if denominator else None


def ratio_from_sums(rows: list[dict[str, Any]], numerator_field: str, denominator_field: str = "cost") -> float | None:
    numerator = sum(as_num(row.get(numerator_field)) or 0 for row in rows)
    denominator = sum(as_num(row.get(denominator_field)) or 0 for row in rows)
    return numerator / denominator if denominator else None


def bar(value: float, max_value: float, width: int = 22, fill: str = "█") -> str:
    if max_value <= 0:
        return ""
    count = max(1 if value > 0 else 0, round(value / max_value * width))
    return fill * count


def sparkline(values: list[float | None]) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    nums = [0 if value is None else max(0, value) for value in values]
    max_value = max(nums) if nums else 0
    if max_value <= 0:
        return " → ".join("·" for _ in nums)
    return " → ".join(blocks[min(len(blocks) - 1, round(value / max_value * (len(blocks) - 1)))] for value in nums)


def column_index_from_letter(column: str) -> int:
    index = 0
    for char in column.upper():
        if "A" <= char <= "Z":
            index = index * 26 + ord(char) - ord("A") + 1
    return index


def api(method: str, url: str, *, headers=None, check=True, **kwargs):
    last_error = None
    for attempt in range(4):
        try:
            response = requests.request(method, url, headers=headers, timeout=45, **kwargs)
            break
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(2 + attempt * 2)
    else:
        raise last_error
    response.raise_for_status()
    payload = response.json()
    if check and payload.get("code") != 0:
        raise RuntimeError({"method": method, "url": url, "payload": payload})
    return payload


def sheets_batch(sync, headers: dict[str, str], spreadsheet_token: str, requests_payload: list[dict[str, Any]]):
    return api(
        "POST",
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/sheets_batch_update",
        headers={**headers, "Content-Type": "application/json"},
        json={"requests": requests_payload},
    )


def query_sheets_with_retry(sync, headers: dict[str, str], spreadsheet_token: str):
    last_error = None
    for attempt in range(6):
        try:
            return sync.query_sheets(headers, spreadsheet_token)
        except Exception as exc:
            last_error = exc
            time.sleep(3 + attempt * 2)
    raise last_error


def copy_sheet(sync, headers: dict[str, str], spreadsheet_token: str, source_sheet_id: str, title: str) -> str:
    payload = sheets_batch(
        sync,
        headers,
        spreadsheet_token,
        [{"copySheet": {"source": {"sheetId": source_sheet_id}, "destination": {"title": title}}}],
    )
    return payload["data"]["replies"][0]["copySheet"]["properties"]["sheetId"]


def delete_sheet(sync, headers: dict[str, str], spreadsheet_token: str, sheet_id: str):
    sheets_batch(sync, headers, spreadsheet_token, [{"deleteSheet": {"sheetId": sheet_id}}])


def resolve_template_sheet_id(sync, headers: dict[str, str], spreadsheet_token: str, config: dict[str, Any]) -> str:
    if config.get("template_source") == "first_sheet":
        sheets = query_sheets_with_retry(sync, headers, spreadsheet_token)
        if not sheets:
            raise RuntimeError("No sheets found in destination spreadsheet.")
        first = min(sheets, key=lambda item: int(item.get("index", 0)))
        return str(first["sheet_id"])
    return str(config.get("template_sheet_id", "2f8lOw"))


def clear_template_data(sync, headers: dict[str, str], spreadsheet_token: str, sheet_id: str, end_row: int):
    sync.update_values(headers, spreadsheet_token, f"{sheet_id}!A3:AZ{end_row}", [[""] * 52 for _ in range(end_row - 2)])


def normalize_file(sync, file_task: dict[str, str], target_headers: list[str], infer_channel_from_ad: bool = False, expected_platform: str | None = None):
    raw_rows = sync.load_export(Path(file_task["path"]))
    for raw_row in raw_rows:
        if raw_row.get("后续ROI") in (None, "") and raw_row.get("累计付费roi") not in (None, ""):
            raw_row["后续ROI"] = raw_row["累计付费roi"]
        if raw_row.get("付费人数") in (None, "") and raw_row.get("付费累计人数") not in (None, ""):
            raw_row["付费人数"] = raw_row["付费累计人数"]
        if raw_row.get("总付费人数") in (None, "") and raw_row.get("付费累计人数") not in (None, ""):
            raw_row["总付费人数"] = raw_row["付费累计人数"]
        if raw_row.get("单次付费人数费用") in (None, "") and raw_row.get("总付费人数成本") not in (None, ""):
            raw_row["单次付费人数费用"] = raw_row["总付费人数成本"]
        if raw_row.get("总付费次数费用") in (None, "") and raw_row.get("总付费次数成本-XY") not in (None, ""):
            raw_row["总付费次数费用"] = raw_row["总付费次数成本-XY"]
        if raw_row.get("总付费次数") in (None, "") and raw_row.get("总付费次数-XY") not in (None, ""):
            raw_row["总付费次数"] = raw_row["总付费次数-XY"]
        if raw_row.get("总付费次数成本") in (None, "") and raw_row.get("总付费次数成本-XY") not in (None, ""):
            raw_row["总付费次数成本"] = raw_row["总付费次数成本-XY"]
        if raw_row.get("D0付费次数") in (None, "") and raw_row.get("首日付费次数-XY") not in (None, ""):
            raw_row["D0付费次数"] = raw_row["首日付费次数-XY"]
        if raw_row.get("D0付费次数成本") in (None, "") and raw_row.get("首日付费次数成本-XY") not in (None, ""):
            raw_row["D0付费次数成本"] = raw_row["首日付费次数成本-XY"]
        if raw_row.get("次日留存") in (None, "") and raw_row.get("Retention rate 1 days on-period appsflyer") not in (None, ""):
            raw_row["次日留存"] = raw_row["Retention rate 1 days on-period appsflyer"]
        if raw_row.get("3日留存") in (None, "") and raw_row.get("Retention rate 3 days on-period appsflyer") not in (None, ""):
            raw_row["3日留存"] = raw_row["Retention rate 3 days on-period appsflyer"]
    normalized_rows, issues, input_mapping = sync.normalize_export_rows(raw_rows, target_headers)
    for raw_row, row in zip(raw_rows, normalized_rows):
        if row.get("后续ROI") in (None, "") and raw_row.get("累计付费roi") not in (None, ""):
            row["后续ROI"] = raw_row["累计付费roi"]
        if row.get("付费人数") in (None, "") and raw_row.get("付费累计人数") not in (None, ""):
            row["付费人数"] = raw_row["付费累计人数"]
        if row.get("单次付费人数费用") in (None, "") and raw_row.get("总付费人数成本") not in (None, ""):
            row["单次付费人数费用"] = raw_row["总付费人数成本"]
        if row.get("总付费人数") in (None, "") and raw_row.get("付费累计人数") not in (None, ""):
            row["总付费人数"] = raw_row["付费累计人数"]
        if row.get("总付费人数成本") in (None, "") and raw_row.get("总付费人数成本") not in (None, ""):
            row["总付费人数成本"] = raw_row["总付费人数成本"]
        if row.get("总付费人数") in (None, "") and row.get("付费人数") not in (None, ""):
            row["总付费人数"] = row["付费人数"]
        if row.get("总付费人数成本") in (None, ""):
            for source_key in ("单次付费人数费用", "总付费人数成本"):
                if row.get(source_key) not in (None, ""):
                    row["总付费人数成本"] = row[source_key]
                    break
        if row.get("总付费次数费用") in (None, "") and raw_row.get("总付费次数成本-XY") not in (None, ""):
            row["总付费次数费用"] = raw_row["总付费次数成本-XY"]
        if row.get("总付费次数") in (None, "") and raw_row.get("总付费次数-XY") not in (None, ""):
            row["总付费次数"] = raw_row["总付费次数-XY"]
        if row.get("总付费次数成本") in (None, "") and raw_row.get("总付费次数成本-XY") not in (None, ""):
            row["总付费次数成本"] = raw_row["总付费次数成本-XY"]
        if row.get("D0付费次数") in (None, "") and raw_row.get("首日付费次数-XY") not in (None, ""):
            row["D0付费次数"] = raw_row["首日付费次数-XY"]
        if row.get("D0付费次数成本") in (None, "") and raw_row.get("首日付费次数成本-XY") not in (None, ""):
            row["D0付费次数成本"] = raw_row["首日付费次数成本-XY"]
        if row.get("次日留存") in (None, "") and raw_row.get("Retention rate 1 days on-period appsflyer") not in (None, ""):
            row["次日留存"] = raw_row["Retention rate 1 days on-period appsflyer"]
        if row.get("3日留存") in (None, "") and raw_row.get("Retention rate 3 days on-period appsflyer") not in (None, ""):
            row["3日留存"] = raw_row["Retention rate 3 days on-period appsflyer"]
        for numeric_key in (
            "总付费人数",
            "总付费次数",
            "D0付费次数",
            "总付费人数成本",
            "总付费次数成本",
            "总付费次数费用",
            "D0付费次数成本",
            "次日留存",
            "3日留存",
        ):
            parsed = as_num(row.get(numeric_key))
            if parsed is not None:
                row[numeric_key] = int(round(parsed)) if numeric_key in INTEGER_HEADERS else parsed
    if expected_platform:
        kept_rows = []
        for row in normalized_rows:
            campaign = str(row.get("广告系列") or "").upper()
            detected = "IOS" if "_IOS_" in campaign else "GP" if "_GP_" in campaign else None
            if detected and detected != expected_platform.upper():
                issues.append({"row": None, "type": "platform_mismatch", "reason": f"Campaign platform {detected} does not match target {expected_platform}", "campaign": row.get("广告系列"), "ad": row.get("Ad")})
                continue
            kept_rows.append(row)
        normalized_rows = kept_rows
    forced_channel = file_task.get("channel")
    if forced_channel and not infer_channel_from_ad:
        for row in normalized_rows:
            row["渠道"] = forced_channel
            row["模式"] = "VO" if forced_channel.lower() in {"tt", "tiktok"} else sync.mode_from_ad(str(row.get("Ad") or ""), forced_channel)
    return raw_rows, normalized_rows, issues, input_mapping


def cost_sort_key(item: dict[str, Any]) -> float:
    cost = item.get("cost")
    return float(cost) if isinstance(cost, (int, float)) else -1


def build_rows(sync, sheet_task: dict[str, Any], target_headers: list[str], infer_channel_from_ad: bool = False):
    raw_count = 0
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    mappings: dict[str, Any] = {}

    for file_task in sheet_task["files"]:
        raw_rows, normalized_rows, file_issues, mapping = normalize_file(sync, file_task, target_headers, infer_channel_from_ad, sheet_task.get("platform"))
        raw_count += len(raw_rows)
        all_rows.extend(normalized_rows)
        issues.extend({"file": file_task["path"], **issue} for issue in file_issues)
        mappings[Path(file_task["path"]).name] = mapping

    seen = set()
    deduped = []
    zero_cost_drops = 0
    duplicate_drops = 0
    blank_row_drops = sum(1 for issue in issues if issue.get("type") == "blank_row")
    for item in all_rows:
        if sync.should_drop_low_spend_blank_revenue(item):
            zero_cost_drops += 1
            issues.append({"row": None, "type": "drop", "reason": "cost is 0", "key": sync.row_key(item)})
            continue
        key = sync.row_key(item)
        if key in seen:
            duplicate_drops += 1
            issues.append({"row": None, "type": "duplicate", "reason": "duplicate in export", "key": key})
            continue
        seen.add(key)
        deduped.append(item)

    deduped.sort(key=cost_sort_key, reverse=True)
    return raw_count, deduped, issues, mappings, zero_cost_drops, duplicate_drops, blank_row_drops


def normalize_ailin2_template_dropdown_values(rows: list[dict[str, Any]], target_headers: list[str]):
    if not {"渠道", "新素材"}.issubset(set(target_headers)):
        return
    for row in rows:
        channel = str(row.get("渠道") or "")
        if channel.lower() in {"facebook", "tiktok"}:
            row["渠道"] = channel.lower()
        freshness = str(row.get("新素材") or "")
        if freshness == "老素材":
            row["新素材"] = "旧素材"
        elif freshness not in {"新素材", "旧素材"}:
            row["新素材"] = "新素材" if "新素材" in str(row.get("广告系列") or "") else "旧素材"


def normalize_ailin2_channel_mode(sync, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        campaign = str(row.get("广告系列") or "")
        ad = str(row.get("Ad") or "")
        channel = sync.channel_from_ad_and_campaign(ad, campaign)
        row["渠道"] = str(channel or "").lower()
        row["模式"] = "VO" if row["渠道"] == "tiktok" else sync.mode_from_ad(ad, channel).upper()


def parse_date_token(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        pass
    try:
        return datetime.strptime(value.replace("/", "-"), "%Y-%m-%d")
    except ValueError:
        return None


def last_ad_date(ad: str) -> datetime | None:
    matches = re.findall(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", str(ad or ""))
    for value in reversed(matches):
        parsed = parse_date_token(value)
        if parsed:
            return parsed
    return None


def apply_freshness_by_ad_date(rows: list[dict[str, Any]], sheet_task: dict[str, Any]) -> None:
    rule = sheet_task.get("freshness_by_ad_date")
    if not rule:
        return
    start = parse_date_token(str(rule.get("start", "")))
    end = parse_date_token(str(rule.get("end", "")))
    if not start or not end:
        return
    for row in rows:
        ad_date = last_ad_date(str(row.get("Ad") or ""))
        row["新素材"] = "新素材" if ad_date and start <= ad_date <= end else "旧素材"


def first_existing_header(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in headers:
            return candidate
    return None


def row_first_number(row: dict[str, Any], candidates: tuple[str, ...]):
    for candidate in candidates:
        value = as_num(row.get(candidate))
        if value is not None:
            return value
    return None


def score_direction(item: dict[str, Any], cpi_threshold: float | None, max_cost: float, rules: dict[str, Any] | None = None) -> float:
    d0 = item["d0_roi"] or 0
    d3 = item["d3_roi"] or 0
    d7 = item["d7_roi"] or 0
    payer_cost = item["payer_cost"]
    cpi = item["cpi"]
    spend = item["cost"]
    payers = item["payers"]
    roi_limits = (rules or {}).get("roi_green", {})
    roi_score = sum(min(value / limit, 2.0) * weight for value, limit, weight in (
        (d0, roi_limits.get("首日ROI", 0.07), 32),
        (d3, roi_limits.get("3日ROI", 0.17), 10),
        (d7, roi_limits.get("7日ROI", 0.35), 8),
    ) if limit)
    payer_limit = (rules or {}).get("payer_cost_green_below", 30)
    payer_score = 0 if payer_cost is None or not payer_limit else min(payer_limit / max(payer_cost, 0.01), 2.0) * 18
    cpi_score = 0 if cpi is None or not cpi_threshold else min(cpi_threshold / max(cpi, 0.01), 2.0) * 12
    spend_share = spend / max_cost if max_cost else 0
    spend_factor = 0.3 if spend < 50 or payers < 3 else 0.55 if spend < 100 else 0.75 + min(spend_share, 1.0) * 0.45
    return round((roi_score + payer_score + cpi_score) * spend_factor, 1)


def recommendation(item: dict[str, Any], cpi_threshold: float | None, max_cost: float, rules: dict[str, Any] | None = None) -> tuple[str, str]:
    d0 = item["d0_roi"]
    d3 = item["d3_roi"]
    payer_cost = item["payer_cost"]
    cpi = item["cpi"]
    spend = item["cost"]
    payers = item["payers"]
    roi_limits = (rules or {}).get("roi_green", {})
    d0_limit = roi_limits.get("首日ROI", 0.07)
    d3_limit = roi_limits.get("3日ROI", 0.17)
    payer_limit = (rules or {}).get("payer_cost_green_below", 30)
    roi_good = d0 is not None and d0_limit is not None and d0 > d0_limit
    payer_good = payer_cost is not None and payer_limit is not None and payer_cost < payer_limit
    cpi_good = cpi is not None and cpi_threshold is not None and cpi < cpi_threshold
    high_spend = spend >= 100 and (spend >= max_cost * 0.35 if max_cost else True)
    sample_small = spend < 100 or payers < 3
    if roi_good and high_spend and payer_good:
        return "重点放量", "花费规模和首日 ROI 同时较好，优先保留核心卖点并扩同方向素材。"
    if roi_good and high_spend:
        return "放量", "花费规模足够且 ROI 能承接，继续扩同方向并优化付费成本。"
    if sample_small:
        return ("补样本", "指标有亮点但样本偏小，先小额补量验证稳定性。") if (roi_good or payer_good or cpi_good) else ("补样本", "样本偏小，先补到可判断花费后再决定。")
    if roi_good and payer_good:
        return "放量", "保留核心卖点，扩同素材结构与相近人群。"
    if roi_good:
        return "提效后放量", "ROI 能承接，重点降低首付成本并优化前 5 秒。"
    if payer_good and d3_limit is not None and (d3 or 0) > d3_limit:
        return "观察放量", "付费成本低且 3 日回收可看，追加小预算验证。"
    if cpi_good and not roi_good:
        return "只做探索", "获客便宜但回收不足，适合做前置开头素材池。"
    return "暂停或重做", "ROI 与成本都不占优，建议换卖点或换开头。"


def detailed_material_direction(row: dict[str, Any]) -> str:
    """Use the detailed concept in the Ad description, merging numbered variants."""
    ad = str(row.get("Ad") or "").strip()
    match = re.search(r"[（(]([^）)]+)", ad)
    if not match:
        return str(row.get("素材方向") or "未识别").strip() or "未识别"
    parts = [part.strip() for part in match.group(1).split("_")]
    material = "_".join(parts[2:]).strip() if len(parts) >= 3 else match.group(1).strip()
    material = re.sub(r"[.。]+$", "", material).strip()
    material = re.sub(r"\d+$", "", material).strip()
    return material or str(row.get("素材方向") or "未识别").strip() or "未识别"


def build_analysis(sync, rows: list[dict[str, Any]], platform: str, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        material = detailed_material_direction(row)
        groups[material].append(row)

    rules = profile["quality"][platform] if profile else None
    cpi_threshold = rules["cpi_code_below"] if rules else sync.PLATFORM_CPI_THRESHOLDS[platform]
    analysis = []
    max_cost = max((sum(as_num(row.get("cost")) or 0 for row in group_rows) for group_rows in groups.values()), default=0)
    for material, group_rows in groups.items():
        cost = sum(as_num(row.get("cost")) or 0 for row in group_rows)
        revenue = sum(as_num(row.get("累计收入")) or 0 for row in group_rows)
        payers = sum(as_num(row.get("付费人数")) or 0 for row in group_rows)
        installs = sum(as_num(row.get("安装")) or 0 for row in group_rows)
        item = {
            "material": material,
            "rows": len(group_rows),
            "cost": cost,
            "revenue": revenue,
            "payers": payers,
            "d0_roi": ratio_from_sums(group_rows, "首充"),
            "d3_roi": inferred_roi_from_cost(group_rows, "3日ROI"),
            "d7_roi": inferred_roi_from_cost(group_rows, "7日ROI"),
            "d30_roi": inferred_roi_from_cost(group_rows, "30日ROI"),
            "payer_cost": safe_div(cost, payers),
            "cpi": safe_div(cost, installs),
        }
        if rules:
            item["quality_hits"] = sum(code_color(row, rules) is not None for row in group_rows)
        else:
            item["quality_hits"] = sum(
                1 for row in group_rows
                if (as_num(row.get("首日ROI")) or 0) > 0.07
                or (row_first_number(row, PAYER_COST_HEADERS) or 999999) < 30
                or (as_num(row.get("CPI")) or 999999) < cpi_threshold
            )
        item["score"] = score_direction(item, cpi_threshold, max_cost, rules)
        item["action"], item["iteration"] = recommendation(item, cpi_threshold, max_cost, rules)
        analysis.append(item)
    analysis.sort(key=lambda item: item["cost"], reverse=True)
    return analysis


def make_analysis_output(title: str, analysis: list[dict[str, Any]]) -> list[list[Any]]:
    top = analysis[0] if analysis else None
    total_cost = sum(item["cost"] for item in analysis)
    total_revenue = sum(item["revenue"] for item in analysis)
    scale_count = sum(1 for item in analysis if item["action"] in {"放量", "提效后放量", "观察放量"})
    output = [
        [f"{title} 素材方向分析", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["分析时间", datetime.now().strftime("%Y-%m-%d %H:%M"), "方向数", len(analysis), "总花费", total_cost, "累计收入", total_revenue, "建议继续迭代方向数", scale_count, "", "", ""],
        ["结论", f"当前花费最高方向为 {top['material'] if top else ''}；主表按花费降序，建议结合 ROI、付费成本、CPI 与样本规模判断。", "", "", "", "", "", "", "", "", "", "", ""],
        [],
        ["素材方向", "素材条数", "花费", "首日ROI", "3日ROI", "7日ROI", "30日ROI", "单次付费人数费用", "CPI", "优质命中", "综合分", "建议", "迭代方向"],
    ]
    for item in analysis:
        output.append([item["material"], item["rows"], item["cost"], item["d0_roi"], item["d3_roi"], item["d7_roi"], item["d30_roi"], item["payer_cost"], item["cpi"], item["quality_hits"], item["score"], item["action"], item["iteration"]])

    output += [[], ["可视化图表区", "", "", "", "", "", "", "", "", "", "", "", ""]]
    spend_top = sorted(analysis, key=lambda item: item["cost"], reverse=True)[:10]
    max_spend = max((item["cost"] for item in spend_top), default=0)
    output.append(["花费柱状图 Top10", "花费", "占比/柱状图", "", "", "", "", "", "", "", "", "", ""])
    for item in spend_top:
        share = item["cost"] / total_cost if total_cost else 0
        output.append([item["material"], item["cost"], f"{share:.1%} {bar(item['cost'], max_spend)}", "", "", "", "", "", "", "", "", "", ""])
    output += [[], ["ROI 趋势折线图 Top10", "首日ROI", "3日ROI", "7日ROI", "30日ROI", "趋势", "建议", "", "", "", "", "", ""]]
    for item in analysis[:10]:
        output.append([item["material"], item["d0_roi"], item["d3_roi"], item["d7_roi"], item["d30_roi"], sparkline([item["d0_roi"], item["d3_roi"], item["d7_roi"], item["d30_roi"]]), item["action"], "", "", "", "", "", ""])
    output.append([])
    action_counts: dict[str, int] = {}
    for item in analysis:
        action_counts[item["action"]] = action_counts.get(item["action"], 0) + 1
    max_count = max(action_counts.values(), default=0)
    output.append(["建议动作占比（饼图数据）", "方向数", "占比", "占比图", "", "", "", "", "", "", "", "", ""])
    for action, count in sorted(action_counts.items(), key=lambda pair: pair[1], reverse=True):
        output.append([action, count, count / len(analysis) if analysis else 0, bar(count, max_count, width=18, fill="●"), "", "", "", "", "", "", "", "", ""])
    return output


def write_visualization(sync, headers, spreadsheet_token: str, sheet_id: str, title: str, platform: str, target_headers: list[str], rows: list[dict[str, Any]], start_col: str = "AE", profile: dict[str, Any] | None = None):
    analysis = build_analysis(sync, rows, platform, profile)
    output = make_analysis_output(title, analysis)
    if profile:
        preferred = tuple(profile["quality"][platform].get("payer_cost_headers", ()))
        payer_cost_label = first_existing_header(target_headers, preferred)
        if payer_cost_label:
            output[4][7] = payer_cost_label
    start = 2
    end_col = sync.column_letter(column_index_from_letter(start_col) + 12)
    sync.update_values(headers, spreadsheet_token, f"{sheet_id}!{start_col}{start}:{end_col}{start + 179}", [[""] * 13 for _ in range(180)])
    sync.update_values(headers, spreadsheet_token, f"{sheet_id}!{start_col}{start}:{end_col}{start + len(output) - 1}", output)
    spend_rows = min(10, len(analysis))
    trend_rows = min(10, len(analysis))
    spend_start = start + len(analysis) + 8
    trend_start = start + len(analysis) + spend_rows + 10
    action_start = trend_start + trend_rows + 2
    start_index = column_index_from_letter(start_col)
    col = lambda offset: sync.column_letter(start_index + offset)
    table_header_row = start + 4
    table_data_start = start + 5
    table_data_end = start + len(analysis) + 4
    visual_title_row = spend_start - 2
    spend_header_row = spend_start - 1
    trend_header_row = trend_start - 1
    action_header_row = action_start - 1
    analysis_end = start + len(output) - 1
    style_data = [
        {"ranges": [f"{sheet_id}!{start_col}{start}:{end_col}{analysis_end}"], "style": {"font": {"bold": False, "clean": False}, "fontSize": "11pt/1.5", "backColor": "#ffffff", "foreColor": "#000000", "hAlign": 1, "vAlign": 1, "clean": False}},
        {"ranges": [f"{sheet_id}!{start_col}{start}:{end_col}{start}"], "style": {"font": {"bold": True, "clean": False}, "fontSize": "11pt/1.5", "backColor": "#d9ead3", "hAlign": 0, "clean": False}},
        {"ranges": [f"{sheet_id}!{start_col}{start + 1}:{end_col}{start + 1}"], "style": {"font": {"bold": True, "clean": False}, "backColor": "#f2f3f5", "hAlign": 0, "clean": False}},
        {"ranges": [f"{sheet_id}!{start_col}{start + 2}:{end_col}{start + 2}"], "style": {"font": {"bold": False, "clean": False}, "backColor": "#fff2cc", "hAlign": 0, "clean": False}},
        {"ranges": [f"{sheet_id}!{start_col}{table_header_row}:{end_col}{table_header_row}"], "style": {"font": {"bold": True, "clean": False}, "backColor": "#4e7efc", "foreColor": "#ffffff", "hAlign": 1, "clean": False}},
        {"ranges": [f"{sheet_id}!{start_col}{visual_title_row}:{end_col}{visual_title_row}"], "style": {"font": {"bold": True, "clean": False}, "backColor": "#d9ead3", "hAlign": 0, "clean": False}},
        {"ranges": [f"{sheet_id}!{start_col}{spend_header_row}:{end_col}{spend_header_row}", f"{sheet_id}!{start_col}{trend_header_row}:{end_col}{trend_header_row}", f"{sheet_id}!{start_col}{action_header_row}:{end_col}{action_header_row}"], "style": {"font": {"bold": True, "clean": False}, "backColor": "#eef3ea", "hAlign": 0, "clean": False}},
        {"ranges": [f"{sheet_id}!{start_col}{table_data_start}:{start_col}{analysis_end}", f"{sheet_id}!{col(11)}{table_data_start}:{col(12)}{table_data_end}"], "style": {"hAlign": 0}},
        {"ranges": [f"{sheet_id}!{col(3)}{start + 5}:{col(6)}{start + len(analysis) + 4}"], "style": {"formatter": "0.00%"}},
        {"ranges": [f"{sheet_id}!{col(1)}{trend_start}:{col(4)}{trend_start + trend_rows - 1}"], "style": {"formatter": "0.00%"}},
        {"ranges": [f"{sheet_id}!{col(2)}{start + 5}:{col(2)}{start + len(analysis) + 4}", f"{sheet_id}!{col(7)}{start + 5}:{col(8)}{start + len(analysis) + 4}", f"{sheet_id}!{col(1)}{spend_start}:{col(1)}{spend_start + spend_rows - 1}", f"{sheet_id}!{col(5)}{start + 1}:{col(5)}{start + 1}", f"{sheet_id}!{col(7)}{start + 1}:{col(7)}{start + 1}"], "style": {"formatter": "$#,##0.00"}},
        {"ranges": [f"{sheet_id}!{col(1)}{table_data_start}:{col(1)}{table_data_end}", f"{sheet_id}!{col(9)}{table_data_start}:{col(9)}{table_data_end}"], "style": {"formatter": "#,##0"}},
        {"ranges": [f"{sheet_id}!{col(10)}{table_data_start}:{col(10)}{table_data_end}"], "style": {"formatter": "#,##0.00"}},
        {"ranges": [f"{sheet_id}!{col(2)}{action_start}:{col(2)}{start + len(output) - 1}"], "style": {"formatter": "0.00%"}},
    ]
    try:
        sync.set_styles(headers, spreadsheet_token, style_data)
    except Exception:
        pass
    return {
        "analysis_start_cell": f"{start_col}{start}",
        "analysis_start_row": start,
        "analysis_end_row": analysis_end,
        "directions": len(analysis),
        "top_direction": analysis[0]["material"] if analysis else "",
    }


def trim_trailing_blank_rows(headers, spreadsheet_token: str, sheet_id: str, keep_rows: int):
    sheets = requests.get(
        f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query",
        headers=headers,
        timeout=30,
    )
    sheets.raise_for_status()
    payload = sheets.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Query sheets error before row trim: {payload}")
    sheet = next(item for item in payload["data"]["sheets"] if item["sheet_id"] == sheet_id)
    row_count = int(sheet.get("grid_properties", {}).get("row_count") or keep_rows)
    keep_rows = max(1, int(keep_rows))
    if row_count <= keep_rows:
        return {"before": row_count, "after": row_count, "deleted": 0}

    response = requests.delete(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "dimension": {
                "sheetId": sheet_id,
                "majorDimension": "ROWS",
                "startIndex": keep_rows + 1,
                "endIndex": row_count,
            }
        },
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Delete trailing rows error: {result}")
    return {
        "before": row_count,
        "after": keep_rows,
        "deleted": row_count - keep_rows,
    }


def ensure_sheet_row_capacity(headers, spreadsheet_token: str, sheet_id: str, required_rows: int):
    sheet = next(
        item
        for item in requests.get(
            f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query",
            headers=headers,
            timeout=30,
        ).json()["data"]["sheets"]
        if item["sheet_id"] == sheet_id
    )
    row_count = int(sheet.get("grid_properties", {}).get("row_count") or 1)
    required_rows += 1
    if row_count >= required_rows:
        return
    response = requests.post(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "dimension": {
                "sheetId": sheet_id,
                "majorDimension": "ROWS",
                "length": required_rows - row_count,
            }
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Add rows error: {payload}")


def apply_extended_data_format(
    sync,
    headers,
    spreadsheet_token: str,
    sheet_id: str,
    target_headers: list[str],
    last_row: int,
):
    start_row = 110
    if last_row < start_row:
        return
    response = requests.put(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "dimension": {
                "sheetId": sheet_id,
                "majorDimension": "ROWS",
                "startIndex": start_row,
                "endIndex": last_row,
            },
            "dimensionProperties": {"fixedSize": 24},
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Set extended row height error: {payload}")

    styles = [
        {
            "ranges": [f"{sheet_id}!A{start_row}:{sync.column_letter(len(target_headers))}{last_row}"],
            "style": {
                "font": {"bold": False, "clean": False},
                "fontSize": "11pt/1.5",
                "hAlign": 1,
                "vAlign": 1,
                "backColor": sync.DATA_WHITE,
                "foreColor": sync.FONT_BLACK,
                "clean": False,
            },
        }
    ]
    for index, header in enumerate(target_headers, start=1):
        column = sync.column_letter(index)
        if header in PERCENT_HEADERS:
            formatter = "0.00%"
        elif header in MONEY_HEADERS:
            formatter = "#,##0.00"
        elif header in INTEGER_HEADERS:
            formatter = "#,##0"
        else:
            continue
        styles.append(
            {
                "ranges": [f"{sheet_id}!{column}{start_row}:{column}{last_row}"],
                "style": {"formatter": formatter},
            }
        )
    try:
        sync.set_styles(headers, spreadsheet_token, styles)
    except Exception:
        pass


def apply_shenyu_weekly_data_format(sync, headers, spreadsheet_token: str, sheet_id: str, last_row: int):
    if last_row < sync.DATA_START_ROW:
        return
    response = requests.put(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "dimension": {
                "sheetId": sheet_id,
                "majorDimension": "ROWS",
                "startIndex": sync.DATA_START_ROW,
                "endIndex": last_row,
            },
            "dimensionProperties": {"fixedSize": 24},
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Set Shenyu row height error: {payload}")

    style_payload = [
        {
            "ranges": [f"{sheet_id}!A{sync.DATA_START_ROW}:AC{last_row}"],
            "style": {
                "font": {"bold": False, "clean": False},
                "fontSize": "11pt/1.5",
                "hAlign": 1,
                "vAlign": 1,
                "backColor": sync.DATA_WHITE,
                "foreColor": sync.FONT_BLACK,
                "clean": False,
            },
        },
        {
            "ranges": [
                f"{sheet_id}!F{sync.DATA_START_ROW}:G{last_row}",
                f"{sheet_id}!N{sync.DATA_START_ROW}:N{last_row}",
                f"{sheet_id}!P{sync.DATA_START_ROW}:P{last_row}",
                f"{sheet_id}!R{sync.DATA_START_ROW}:R{last_row}",
                f"{sheet_id}!T{sync.DATA_START_ROW}:T{last_row}",
                f"{sheet_id}!W{sync.DATA_START_ROW}:X{last_row}",
                f"{sheet_id}!AC{sync.DATA_START_ROW}:AC{last_row}",
            ],
            "style": {"formatter": "$#,##0.00"},
        },
        {
            "ranges": [
                f"{sheet_id}!H{sync.DATA_START_ROW}:M{last_row}",
                f"{sheet_id}!U{sync.DATA_START_ROW}:V{last_row}",
                f"{sheet_id}!Y{sync.DATA_START_ROW}:Y{last_row}",
            ],
            "style": {"formatter": "0.00%"},
        },
        {
            "ranges": [
                f"{sheet_id}!O{sync.DATA_START_ROW}:O{last_row}",
                f"{sheet_id}!Q{sync.DATA_START_ROW}:Q{last_row}",
                f"{sheet_id}!S{sync.DATA_START_ROW}:S{last_row}",
                f"{sheet_id}!Z{sync.DATA_START_ROW}:AB{last_row}",
            ],
            "style": {"formatter": "#,##0"},
        },
    ]
    sync.set_styles(headers, spreadsheet_token, style_payload)


def apply_ailin2_template_dropdown_validation(sync, headers, spreadsheet_token: str, sheet_id: str, target_headers: list[str], last_row: int):
    if last_row < 3:
        return
    header_to_col = {header: sync.column_letter(index + 1) for index, header in enumerate(target_headers)}
    url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dataValidation"
    request_headers = {**headers, "Content-Type": "application/json"}
    rules = [
        {
            "range": f"{sheet_id}!{header_to_col['渠道']}3:{header_to_col['渠道']}{last_row}",
            "dataValidationType": "list",
            "dataValidation": {
                "conditionValues": ["tiktok", "facebook"],
                "options": {"highlightValidData": True, "multipleValues": False, "colors": ["#bacefd", "#fed4a4"]},
            },
        },
        {
            "range": f"{sheet_id}!{header_to_col['素材类型']}3:{header_to_col['素材类型']}{last_row}",
            "dataValidationType": "list",
            "dataValidation": {
                "conditionValues": ["图片", "视频"],
                "options": {"highlightValidData": True, "multipleValues": False, "colors": ["#bacefd", "#fed4a4"]},
            },
        },
        {
            "range": f"{sheet_id}!{header_to_col['新素材']}3:{header_to_col['新素材']}{last_row}",
            "dataValidationType": "list",
            "dataValidation": {
                "conditionValues": ["新素材", "旧素材"],
                "options": {"highlightValidData": True, "multipleValues": False, "colors": ["#2ea121", "#f54a45"]},
            },
        },
    ]
    mode_header = "\u6a21\u5f0f"
    if mode_header in header_to_col:
        mode_col = header_to_col[mode_header]
        rules.insert(
            1,
            {
                "range": f"{sheet_id}!{mode_col}3:{mode_col}{last_row}",
                "dataValidationType": "list",
                "dataValidation": {
                    "conditionValues": ["MAI", "AEO", "VO"],
                    "options": {"highlightValidData": True, "multipleValues": False, "colors": ["#fed4a4", "#bacefd", "#d9ead3"]},
                },
            },
        )
    for rule in rules:
        try:
            response = requests.post(url, headers=request_headers, json=rule, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                continue
        except Exception:
            continue


def apply_ailin2_template_data_format(sync, headers, spreadsheet_token: str, sheet_id: str, target_headers: list[str], last_row: int):
    if last_row < sync.DATA_START_ROW:
        return
    response = requests.put(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "dimension": {
                "sheetId": sheet_id,
                "majorDimension": "ROWS",
                "startIndex": sync.DATA_START_ROW,
                "endIndex": last_row,
            },
            "dimensionProperties": {"fixedSize": 24},
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Set AILIN2 row height error: {payload}")

    style_payload = [
        {
            "ranges": [f"{sheet_id}!A{sync.DATA_START_ROW}:{sync.column_letter(len(target_headers))}{last_row}"],
            "style": {
                "font": {"bold": False, "clean": False},
                "fontSize": "11pt/1.5",
                "hAlign": 1,
                "vAlign": 1,
                "backColor": "#ffffff",
                "foreColor": "#000000",
                "clean": False,
            },
        },
    ]
    for formatter, names in (
        ("$#,##0.00", MONEY_HEADERS),
        ("0.00%", PERCENT_HEADERS),
        ("#,##0", INTEGER_HEADERS),
    ):
        ranges = []
        for index, header in enumerate(target_headers, start=1):
            if header in names:
                col = sync.column_letter(index)
                ranges.append(f"{sheet_id}!{col}{sync.DATA_START_ROW}:{col}{last_row}")
        if ranges:
            style_payload.append({"ranges": ranges, "style": {"formatter": formatter}})
    left_align_headers = {"\u7f16\u53f7", "Ad", "\u5e7f\u544a\u7cfb\u5217"}
    left_ranges = []
    for index, header in enumerate(target_headers, start=1):
        if header in left_align_headers:
            col = sync.column_letter(index)
            left_ranges.append(f"{sheet_id}!{col}{sync.DATA_START_ROW}:{col}{last_row}")
    if left_ranges:
        style_payload.append({"ranges": left_ranges, "style": {"hAlign": 0}})
    sync.set_styles(headers, spreadsheet_token, style_payload)


def ailin2_quality_cell_ranges(sync, rows: list[tuple[int, list[Any]]], target_headers: list[str], sheet_id: str, platform: str) -> list[str]:
    payer_cost_header = first_existing_header(target_headers, PAYER_COST_HEADERS)
    payer_cost_idx = target_headers.index(payer_cost_header) if payer_cost_header else None
    cpi_idx = target_headers.index("CPI")
    roi_indices = {name: target_headers.index(name) for name in AILIN2_DATA_ROI_THRESHOLDS}
    ranges = []
    payer_cost_threshold = AILIN2_PAYER_COST_THRESHOLDS.get(platform, 30)
    for row_num, values in rows:
        payer_cost = values[payer_cost_idx] if payer_cost_idx is not None else None
        cpi = values[cpi_idx]
        if isinstance(payer_cost, (int, float)) and payer_cost < payer_cost_threshold:
            col = sync.column_letter(payer_cost_idx + 1)
            ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
        if platform == "IOS" and isinstance(cpi, (int, float)) and cpi < 10:
            col = sync.column_letter(cpi_idx + 1)
            ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
        for roi_name, threshold in AILIN2_DATA_ROI_THRESHOLDS.items():
            roi = values[roi_indices[roi_name]]
            if isinstance(roi, (int, float)) and roi > threshold:
                col = sync.column_letter(roi_indices[roi_name] + 1)
                ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
    return ranges


def apply_ailin2_code_priority_highlight(sync, headers, spreadsheet_token: str, sheet_id: str, rows: list[tuple[int, list[Any]]], target_headers: list[str], platform: str):
    if not rows:
        return
    code_idx = target_headers.index("编号")
    roi_idx = target_headers.index("首日ROI")
    d3_roi_idx = target_headers.index("3日ROI")
    d7_roi_idx = target_headers.index("7日ROI")
    d14_roi_idx = target_headers.index("14日ROI")
    later_roi_idx = target_headers.index("后续ROI") if "后续ROI" in target_headers else None
    payer_cost_header = first_existing_header(target_headers, PAYER_COST_HEADERS)
    payer_cost_idx = target_headers.index(payer_cost_header) if payer_cost_header else None
    cpi_idx = target_headers.index("CPI")
    mode_idx = target_headers.index("模式") if "模式" in target_headers else None
    ad_idx = target_headers.index("Ad") if "Ad" in target_headers else None
    code_col = sync.column_letter(code_idx + 1)
    cpi_threshold = AILIN2_CODE_CPI_THRESHOLDS[platform]
    payer_cost_threshold = AILIN2_PAYER_COST_THRESHOLDS.get(platform, 30)

    all_rows = []
    green_rows = []
    blue_rows = []
    yellow_rows = []
    red_rows = []
    for row_num, values in rows:
        if values[code_idx] in (None, ""):
            continue
        all_rows.append(row_num)
        roi = values[roi_idx]
        d3_roi = values[d3_roi_idx]
        d7_roi = values[d7_roi_idx]
        d14_roi = values[d14_roi_idx]
        later_roi = values[later_roi_idx] if later_roi_idx is not None else None
        payer_cost = values[payer_cost_idx] if payer_cost_idx is not None else None
        cpi = values[cpi_idx]
        if mode_idx is not None:
            mode = str(values[mode_idx] or "").upper()
        elif ad_idx is not None:
            ad = str(values[ad_idx] or "")
            campaign_idx = target_headers.index("广告系列") if "广告系列" in target_headers else None
            campaign = str(values[campaign_idx] or "") if campaign_idx is not None else ""
            mode = sync.mode_from_ad(ad, sync.channel_from_ad_and_campaign(ad, campaign)).upper()
        else:
            mode = ""
        if isinstance(roi, (int, float)) and roi > 0.09:
            green_rows.append(row_num)
        elif (
            (not isinstance(roi, (int, float)) or roi <= 0.09)
            and (
                (isinstance(d3_roi, (int, float)) and d3_roi > 0.28)
                or (isinstance(d7_roi, (int, float)) and d7_roi > 0.48)
                or (isinstance(d14_roi, (int, float)) and d14_roi > 0.73)
                or (isinstance(later_roi, (int, float)) and later_roi > 1.45)
            )
        ):
            blue_rows.append(row_num)
        elif isinstance(payer_cost, (int, float)) and payer_cost < payer_cost_threshold:
            yellow_rows.append(row_num)
        elif mode != "MAI" and isinstance(cpi, (int, float)) and cpi < cpi_threshold:
            red_rows.append(row_num)

    style_payload = []
    white_ranges = sync.contiguous_ranges(all_rows, sheet_id, code_col, code_col)
    if white_ranges:
        style_payload.append(
            {
                "ranges": white_ranges,
                "style": {
                    "font": {"bold": False, "clean": False},
                    "backColor": sync.DATA_WHITE,
                    "foreColor": sync.FONT_BLACK,
                    "clean": False,
                },
            }
        )
    for rows_to_color, color in (
        (green_rows, sync.QUALITY_GREEN),
        (blue_rows, AILIN2_CODE_LATE_BLUE),
        (yellow_rows, sync.QUALITY_YELLOW),
        (red_rows, sync.QUALITY_RED),
    ):
        ranges = sync.contiguous_ranges(rows_to_color, sheet_id, code_col, code_col)
        if ranges:
            style_payload.append(
                {
                    "ranges": ranges,
                    "style": {
                        "font": {"bold": False, "clean": False},
                        "backColor": color,
                        "foreColor": sync.FONT_BLACK,
                        "clean": False,
                    },
                }
            )
    sync.set_styles(headers, spreadsheet_token, style_payload)


def verify_cost_desc(sync, headers, spreadsheet_token: str, sheet_id: str, row_count: int):
    values = sync.read_sheet_values(headers, spreadsheet_token, sheet_id, end_row=row_count + 5)
    target_headers = [str(value or "").strip() for value in values[sync.HEADER_ROW - 1]]
    while target_headers and not target_headers[-1]:
        target_headers.pop()
    cost_idx = target_headers.index("cost")
    ad_idx = target_headers.index("Ad")
    costs = []
    for row in values[sync.DATA_START_ROW - 1 :]:
        padded = row + [None] * max(0, len(target_headers) - len(row))
        if padded[ad_idx]:
            costs.append(as_num(padded[cost_idx]) or -1)
    return {"sheet_rows_with_ad": len(costs), "is_cost_desc": all(costs[i] >= costs[i + 1] for i in range(len(costs) - 1)), "top_10_costs": costs[:10]}


def shenyu_turkey_quality_cell_ranges(sync, rows, target_headers, sheet_id, platform):
    thresholds = {
        "首日ROI": SHENYU_TURKEY_D0_ROI_THRESHOLDS[platform],
        "3日ROI": SHENYU_TURKEY_D3_ROI_THRESHOLDS[platform],
        "7日ROI": SHENYU_TURKEY_D7_ROI_THRESHOLDS[platform],
    }
    ranges = []
    for row_num, values in rows:
        for name, threshold in thresholds.items():
            if name not in target_headers:
                continue
            idx = target_headers.index(name)
            if isinstance(values[idx], (int, float)) and values[idx] > threshold:
                col = sync.column_letter(idx + 1)
                ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
        if "单次付费人数费用" in target_headers:
            idx = target_headers.index("单次付费人数费用")
            if isinstance(values[idx], (int, float)) and values[idx] < SHENYU_TURKEY_DATA_PAYER_COST_THRESHOLDS[platform]:
                col = sync.column_letter(idx + 1)
                ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
        if "CPI" in target_headers:
            idx = target_headers.index("CPI")
            if platform == "IOS" and isinstance(values[idx], (int, float)) and values[idx] < SHENYU_TURKEY_CPI_THRESHOLDS[platform]:
                col = sync.column_letter(idx + 1)
                ranges.append(f"{sheet_id}!{col}{row_num}:{col}{row_num}")
    return ranges


def apply_shenyu_turkey_code_priority(sync, headers, spreadsheet_token, sheet_id, rows, target_headers, platform):
    required = ["编号", "首日ROI", "单次付费人数费用", "CPI"]
    if not rows or any(name not in target_headers for name in required):
        return
    code_idx = target_headers.index("编号")
    roi_idx = target_headers.index("首日ROI")
    payer_idx = target_headers.index("单次付费人数费用")
    cpi_idx = target_headers.index("CPI")
    mode_idx = target_headers.index("模式") if "模式" in target_headers else None
    code_col = sync.column_letter(code_idx + 1)
    d3_idx = target_headers.index("3日ROI") if "3日ROI" in target_headers else None
    d7_idx = target_headers.index("7日ROI") if "7日ROI" in target_headers else None
    all_rows, green_rows, blue_rows, yellow_rows, red_rows = [], [], [], [], []
    for row_num, values in rows:
        if values[code_idx] in (None, ""):
            continue
        all_rows.append(row_num)
        mode = str(values[mode_idx] or "").upper() if mode_idx is not None else ""
        if isinstance(values[roi_idx], (int, float)) and values[roi_idx] > SHENYU_TURKEY_D0_ROI_THRESHOLDS[platform]:
            green_rows.append(row_num)
        elif (
            isinstance(values[roi_idx], (int, float))
            and values[roi_idx] <= SHENYU_TURKEY_CODE_LATE_D0_MAX
            and (
                (d3_idx is not None and isinstance(values[d3_idx], (int, float)) and values[d3_idx] > SHENYU_TURKEY_D3_ROI_THRESHOLDS[platform])
                or (d7_idx is not None and isinstance(values[d7_idx], (int, float)) and values[d7_idx] > SHENYU_TURKEY_D7_ROI_THRESHOLDS[platform])
            )
        ):
            blue_rows.append(row_num)
        elif isinstance(values[payer_idx], (int, float)) and values[payer_idx] < SHENYU_TURKEY_CODE_PAYER_COST_THRESHOLDS[platform]:
            yellow_rows.append(row_num)
        elif mode != "MAI" and isinstance(values[cpi_idx], (int, float)) and values[cpi_idx] < SHENYU_TURKEY_CPI_THRESHOLDS[platform]:
            red_rows.append(row_num)
    style_payload = []
    white_ranges = sync.contiguous_ranges(all_rows, sheet_id, code_col, code_col)
    if white_ranges:
        style_payload.append({"ranges": white_ranges, "style": {"font": {"bold": False, "clean": False}, "backColor": sync.DATA_WHITE, "foreColor": sync.FONT_BLACK, "clean": False}})
    for selected, color in ((green_rows, sync.QUALITY_GREEN), (blue_rows, SHENYU_TURKEY_CODE_LATE_BLUE), (yellow_rows, sync.QUALITY_YELLOW), (red_rows, sync.QUALITY_RED)):
        ranges = sync.contiguous_ranges(selected, sheet_id, code_col, code_col)
        if ranges:
            style_payload.append({"ranges": ranges, "style": {"font": {"bold": False, "clean": False}, "backColor": color, "foreColor": sync.FONT_BLACK, "clean": False}})
    sync.set_styles(headers, spreadsheet_token, style_payload)


def shenyu_turkey_note(pull_time, platform):
    return (
        f"数据拉取时间：{pull_time}；神域-土耳其项目标注说明：数据指标单元格绿色=优质数据"
        "（首日ROI>13%、3日ROI>35%、7日ROI>70%、单次付费人数费用GP<90/iOS<125、IOS CPI<25）；"
        "编号列绿色=首日优秀素材（GP首日ROI>13%,IOS首日ROI>12%），"
        "天蓝色=后续好素材（首日ROI<=12%且3日ROI>35%、7日ROI>70%任一达标），"
        "黄色=付费成本低素材（单次付费人数费用GP<130/iOS<150），"
        "红色=注册单价低素材（CPI低于平台阈值且模式非MAI）；"
        "编号列优先级：绿色>天蓝色>黄色>红色；MAI仅因CPI低不标红。"
    )


def write_shenyu_turkey_rich_note(sync, headers, spreadsheet_token, sheet_id, pull_time):
    template_values = sync.read_sheet_values(headers, spreadsheet_token, SHENYU_TURKEY_BASE_SHEET_ID, end_row=1)
    rich_value = template_values[0][0]
    if not isinstance(rich_value, list) or not rich_value:
        raise RuntimeError("Shenyu Turkey template A1 does not contain the expected rich text")
    rich_value = [dict(segment) for segment in rich_value]
    for segment in rich_value:
        text = str(segment.get("text") or "")
        text = text.replace("首日ROI>13%", "首日ROI>7%")
        text = text.replace("GP首日ROI>13%", "GP首日ROI>7%")
        text = text.replace("IOS首日ROI>12%", "IOS首日ROI>7%")
        text = text.replace("GP首日ROI>13%,IOS首日ROI>12%", "首日ROI>7%")
        text = text.replace("首日ROI<=12%", "首日ROI<=7%")
        text = text.replace("3日ROI>35%", "3日ROI>17%")
        text = text.replace("7日ROI>70%", "7日ROI>35%")
        segment["text"] = text
        style = dict(segment.get("segmentStyle") or {})
        if style.get("foreColor") == "rgb(31, 35, 41)":
            style["foreColor"] = "#1f2329"
        segment["segmentStyle"] = style
    rich_value[0] = {**rich_value[0], "text": f"数据拉取时间：{pull_time}"}
    sync.update_values(headers, spreadsheet_token, f"{sheet_id}!A1:A1", [[rich_value]])


def apply_shenyu_turkey_note_emphasis(sync, headers, spreadsheet_token, sheet_id, target_headers):
    write_shenyu_turkey_rich_note(sync, headers, spreadsheet_token, sheet_id, "2026-07-31 00:00")


def reset_main_table_filter(headers, spreadsheet_token, sheet_id, end_col, last_row):
    url = f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/{sheet_id}/filter"
    request_headers = {**headers, "Content-Type": "application/json"}
    delete_response = requests.delete(url, headers=request_headers, timeout=30)
    if delete_response.status_code not in (200, 400, 404):
        delete_response.raise_for_status()
    response = requests.post(
        url,
        headers=request_headers,
        json={
            "range": f"{sheet_id}!A2:{end_col}{last_row}",
            "col": "A",
            "condition": {"filter_type": "clear"},
        },
        timeout=30,
    )
    if response.status_code in (400, 404):
        return
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        return


def write_sheet(sync, headers, spreadsheet_token: str, config: dict[str, Any], sheet_task: dict[str, Any]):
    profile = config.get("_project_profile")
    if profile:
        validate_active_quality(profile, sheet_task["platform"])
    is_ailin2 = "AILIN2" in str(config.get("game_prefix") or config.get("game") or "").upper()
    game_text = str(config.get("game_prefix") or config.get("game") or "").upper()
    is_shenyu_turkey = ("SHENYU" in game_text and "TURKEY" in game_text) or "神域-土耳其" in game_text or "神域土耳其" in game_text
    existing = {sheet.get("title"): sheet["sheet_id"] for sheet in query_sheets_with_retry(sync, headers, spreadsheet_token)}
    if sheet_task["title"] in existing and config.get("overwrite", True):
        delete_sheet(sync, headers, spreadsheet_token, existing[sheet_task["title"]])
        time.sleep(1)
    template_sheet_id = resolve_template_sheet_id(sync, headers, spreadsheet_token, config)
    sheet_id = copy_sheet(sync, headers, spreadsheet_token, template_sheet_id, sheet_task["title"])
    time.sleep(1)
    clear_to_row = int(config.get("clear_to_row", 900))
    ensure_sheet_row_capacity(headers, spreadsheet_token, sheet_id, clear_to_row)
    clear_template_data(sync, headers, spreadsheet_token, sheet_id, clear_to_row)

    sheet_values = sync.read_sheet_values(headers, spreadsheet_token, sheet_id, end_row=20)
    if config.get("template_header_style") == "shenyu_weekly":
        target_headers = SHENYU_WEEKLY_HEADERS[:]
        sync.update_values(headers, spreadsheet_token, f"{sheet_id}!A2:AD2", [target_headers + [""]])
    else:
        target_headers = [str(x or "").strip() for x in sheet_values[sync.HEADER_ROW - 1]]
        if "Ad" in target_headers:
            ad_index = target_headers.index("Ad")
            first_blank_after_ad = next(
                (index for index in range(ad_index + 1, len(target_headers)) if not target_headers[index]),
                len(target_headers),
            )
            target_headers = target_headers[:first_blank_after_ad]
        else:
            while target_headers and not target_headers[-1]:
                target_headers.pop()
    raw_count, deduped, issues, mappings, zero_cost_drops, duplicate_drops, blank_row_drops = build_rows(
        sync,
        sheet_task,
        target_headers,
        bool(config.get("infer_channel_from_ad")),
    )
    if profile:
        normalize_profile_dropdown_values(deduped, profile)
    if is_ailin2 or is_shenyu_turkey or config.get("template_dropdowns"):
        normalize_ailin2_template_dropdown_values(deduped, target_headers)
    if is_ailin2:
        normalize_ailin2_channel_mode(sync, deduped)
    if config.get("freshness_from_ad_date"):
        apply_freshness_by_ad_date(deduped, sheet_task)
    row_pairs = [(sync.DATA_START_ROW + index, sync.build_sheet_row(sync.DATA_START_ROW + index, item, target_headers)) for index, item in enumerate(deduped)]
    sync.write_rows(headers, spreadsheet_token, sheet_id, row_pairs)
    if is_ailin2:
        pull_time = config.get("pull_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
        sync.update_values(headers, spreadsheet_token, f"{sheet_id}!A1:{sync.column_letter(len(target_headers))}1", [[AILIN2_NOTE_TEMPLATE.format(pull_time=pull_time)] + [""] * (len(target_headers) - 1)])
    elif is_shenyu_turkey:
        pull_time = config.get("pull_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
        write_shenyu_turkey_rich_note(sync, headers, spreadsheet_token, sheet_id, pull_time)
    elif profile:
        pull_time = config.get("pull_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
        note = profile_pull_note(profile, pull_time, sheet_task["platform"])
        sync.update_values(headers, spreadsheet_token, f"{sheet_id}!A1:{sync.column_letter(len(target_headers))}1", [[note] + [""] * (len(target_headers) - 1)])
    else:
        sync.update_pull_note(headers, spreadsheet_token, sheet_id, config.get("pull_time", datetime.now().strftime("%Y-%m-%d %H:%M")), sheet_task["platform"])
    sync.apply_template_layout(headers, spreadsheet_token, sheet_id)
    apply_extended_data_format(
        sync,
        headers,
        spreadsheet_token,
        sheet_id,
        target_headers,
        sync.DATA_START_ROW + len(deduped) - 1,
    )
    if config.get("template_header_style") == "shenyu_weekly":
        apply_shenyu_weekly_data_format(
            sync,
            headers,
            spreadsheet_token,
            sheet_id,
            sync.DATA_START_ROW + len(deduped) - 1,
        )
    if is_ailin2 or is_shenyu_turkey or config.get("template_dropdowns"):
        apply_ailin2_template_data_format(sync, headers, spreadsheet_token, sheet_id, target_headers, sync.DATA_START_ROW + len(deduped) - 1)
    if is_ailin2 or is_shenyu_turkey or config.get("template_dropdowns"):
        apply_ailin2_template_dropdown_validation(sync, headers, spreadsheet_token, sheet_id, target_headers, sync.DATA_START_ROW + len(deduped) - 1)
    elif profile:
        apply_profile_dropdowns(sync, headers, spreadsheet_token, sheet_id, target_headers, sync.DATA_START_ROW + len(deduped) - 1, profile)
    reset_main_table_filter(
        headers,
        spreadsheet_token,
        sheet_id,
        sync.column_letter(len(target_headers)),
        sync.DATA_START_ROW + len(deduped) - 1,
    )
    row_nums = [row_num for row_num, _ in row_pairs]
    if profile:
        hit_ranges = profile_quality_cell_ranges(sync, row_pairs, target_headers, sheet_id, sheet_task["platform"], profile)
    elif is_shenyu_turkey:
        hit_ranges = shenyu_turkey_quality_cell_ranges(sync, row_pairs, target_headers, sheet_id, sheet_task["platform"])
    else:
        hit_ranges = sync.quality_cell_ranges(row_pairs, target_headers, sheet_id, sheet_task["platform"])
    sync.apply_quality_highlight(headers, spreadsheet_token, sheet_id, row_nums, hit_ranges)
    if profile:
        apply_profile_code_highlight(sync, headers, spreadsheet_token, sheet_id, row_pairs, target_headers, sheet_task["platform"], profile)
    elif is_shenyu_turkey:
        apply_shenyu_turkey_code_priority(sync, headers, spreadsheet_token, sheet_id, row_pairs, target_headers, sheet_task["platform"])
    else:
        sync.apply_code_priority_highlight(headers, spreadsheet_token, sheet_id, row_pairs, target_headers, sheet_task["platform"])
    analysis_start_col = sync.column_letter(len(target_headers) + 2)
    if profile and not profile.get("analysis_enabled", False):
        end_col = sync.column_letter(len(target_headers) + 14)
        sync.update_values(headers, spreadsheet_token, f"{sheet_id}!{analysis_start_col}2:{end_col}181", [[""] * 13 for _ in range(180)])
        visual = {"analysis_start_cell": None, "analysis_end_row": 2, "direction_count": 0, "disabled": True}
    else:
        visual = write_visualization(sync, headers, spreadsheet_token, sheet_id, sheet_task["title"], sheet_task["platform"], target_headers, deduped, analysis_start_col, profile)
    row_trim = trim_trailing_blank_rows(
        headers,
        spreadsheet_token,
        sheet_id,
        max(sync.DATA_START_ROW + len(deduped) - 1, visual["analysis_end_row"]),
    )
    verify = verify_cost_desc(sync, headers, spreadsheet_token, sheet_id, len(deduped))
    return {
        "title": sheet_task["title"],
        "sheet_id": sheet_id,
        "input_rows": raw_count,
        "written_rows": len(deduped),
        "zero_cost_drops": zero_cost_drops,
        "blank_row_drops": blank_row_drops,
        "duplicate_drops": duplicate_drops,
        "quality_highlight_cells": len(hit_ranges),
        "field_mapping": mappings,
        "issues": issues,
        "verification": verify,
        "visualization": visual,
        "row_trim": row_trim,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    profile = load_profile(config, args.config)
    if profile is None:
        game_key = str(config.get("game_prefix") or config.get("game") or "UNBOUND_PROJECT").upper()
        raise ValueError(f"{game_key}: complete project onboarding and add project_profile before running; never inherit another project's thresholds")
    config = apply_destination_defaults(apply_profile(config, profile))
    workspace = args.workspace or Path((profile or {}).get("shared_workspace") or Path.cwd())
    sync = load_sync_module(workspace)
    headers = sync.token_headers()
    spreadsheet_token = resolve_destination(sync, headers, config)
    destination_sheets = {
        sheet["sheet_id"] for sheet in query_sheets_with_retry(sync, headers, spreadsheet_token)
    }
    template_sheet_id = resolve_template_sheet_id(sync, headers, spreadsheet_token, config)
    if template_sheet_id not in destination_sheets:
        raise RuntimeError(
            f"Template sheet {template_sheet_id} is not in the configured destination spreadsheet."
        )
    results = [write_sheet(sync, headers, spreadsheet_token, config, sheet_task) for sheet_task in config["sheets"]]
    try:
        sync.sort_week_sheets(headers, spreadsheet_token)
    except Exception:
        pass
    log = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "report_type": config.get("report_type", "weekly"),
        "wiki_token": config.get("wiki_token", ""),
        "results": results,
    }
    history = (profile or {}).get("excellent", {}).get("historical", {})
    if history.get("refresh_script"):
        refresh_on = set(history.get("refresh_on", ["monthly"]))
        eligible_source_written = False
        for task in config["sheets"]:
            title = str(task.get("title") or "")
            source_type = "weekly" if re.match(r"^W\d{1,2}\b", title, re.I) else "monthly" if re.match(r"^M\d{1,2}\b", title, re.I) else None
            if source_type in refresh_on:
                eligible_source_written = True
                break
        if eligible_source_written:
            refresh_script = workspace / history["refresh_script"]
            if not refresh_script.is_file():
                raise FileNotFoundError(f"Historical refresh script not found: {refresh_script}")
            result = subprocess.run([sys.executable, str(refresh_script)], cwd=workspace, capture_output=True, text=True)
            log["historical_refresh"] = {"completed": result.returncode == 0, "details": result.stdout[-4000:], "error": result.stderr[-2000:]}
    output_log = Path(config.get("output_log", f"weekly_game_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"))
    output_log.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False, indent=2))
    if log.get("historical_refresh", {}).get("completed") is False:
        raise RuntimeError("Weekly/monthly source was updated, but historical excellent refresh failed; see output log")


if __name__ == "__main__":
    main()

