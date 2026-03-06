"""
sheets.py — Google Sheets for Personal Task Assistant
columns: Task ID | Title | Category | Assignee | Due Date | Priority | Status | Created | Notes
"""

import json
import logging
from datetime import date, timedelta
from typing import List, Dict, Optional

import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SERVICE_ACCOUNT_JSON

logger = logging.getLogger(__name__)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly"
]
_client: Optional[gspread.Client] = None

CATEGORY_KEYWORDS = {
    "SEO":      ["seo", "keyword", "关键词", "排名", "backlink", "外链", "index", "索引",
                 "google", "search", "搜索", "网站", "site", "domain", "gsc", "t1", "t2", "t3", "pbn"],
    "Social":   ["social", "社交", "facebook", "fb", "instagram", "ig", "tiktok", "telegram",
                 "tg", "whatsapp", "wa", "youtube", "twitter", "post", "发帖", "edm",
                 "engagement", "followers", "粉丝", "内容规划", "kpi"],
    "Ops":      ["ops", "运营", "server", "服务器", "hosting", "billing", "payment", "付款",
                 "invoice", "发票", "team", "团队", "report", "报告", "meeting", "会议",
                 "client", "客户", "campaign", "续费", "renewal"],
    "Personal": ["personal", "个人", "自己", "家", "health", "travel", "旅行",
                 "生日", "birthday", "bank", "银行", "insurance", "私人"],
}

# column index map (1-based)
COL = {
    "task_id":  1,
    "title":    2,
    "category": 3,
    "assignee": 4,
    "due":      5,
    "priority": 6,
    "status":   7,
    "created":  8,
    "notes":    9,
}

FIELD_COL = {
    "title":    COL["title"],
    "category": COL["category"],
    "assignee": COL["assignee"],
    "due":      COL["due"],
    "priority": COL["priority"],
    "status":   COL["status"],
    "notes":    COL["notes"],
}


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        if GOOGLE_SERVICE_ACCOUNT_JSON:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _get_sheet(tab_name: str) -> gspread.Worksheet:
    client = _get_client()
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=10)
        ws.append_row([
            "Task ID", "Title", "Category", "Assignee",
            "Due Date", "Priority", "Status", "Created", "Notes"
        ])
        return ws


def guess_category(text: str) -> str:
    t = text.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in t for k in kws):
            return cat
    return "Ops"


def get_my_tasks(status_filter="pending", category=None, assignee=None) -> List[Dict]:
    try:
        ws = _get_sheet("My Tasks")
        records = ws.get_all_records()
        tasks = [r for r in records if r.get("Task ID")]
        if status_filter == "pending":
            tasks = [r for r in tasks if str(r.get("Status", "")).lower() != "done"]
        elif status_filter == "done":
            tasks = [r for r in tasks if str(r.get("Status", "")).lower() == "done"]
        if category:
            tasks = [r for r in tasks if str(r.get("Category", "")).lower() == category.lower()]
        if assignee:
            tasks = [r for r in tasks if assignee.lower() in str(r.get("Assignee", "")).lower()]
        return tasks
    except Exception as e:
        logger.error(f"get_my_tasks failed: {e}")
        return []


def get_tasks_due_today() -> List[Dict]:
    try:
        today = date.today().isoformat()
        return [t for t in get_my_tasks("pending")
                if str(t.get("Due Date", "")).strip() and str(t.get("Due Date", "")).strip() <= today]
    except Exception as e:
        logger.error(f"get_tasks_due_today failed: {e}")
        return []


def get_tasks_this_week() -> List[Dict]:
    try:
        today    = date.today()
        week_end = today + timedelta(days=(6 - today.weekday()))
        return [t for t in get_my_tasks("all")
                if str(t.get("Due Date", "")).strip() and
                today.isoformat() <= str(t.get("Due Date", "")).strip() <= week_end.isoformat()]
    except Exception as e:
        logger.error(f"get_tasks_this_week failed: {e}")
        return []


def get_tasks_by_date(target_date: str) -> List[Dict]:
    try:
        return [t for t in get_my_tasks("pending")
                if str(t.get("Due Date", "")).strip() == target_date]
    except Exception as e:
        logger.error(f"get_tasks_by_date failed: {e}")
        return []


def write_my_task(task_data: Dict):
    try:
        ws       = _get_sheet("My Tasks")
        all_rows = ws.get_all_values()
        task_num = len([r for r in all_rows if r and str(r[0]).startswith("P")]) + 1
        task_id  = f"P{task_num:03d}"
        category = task_data.get("category") or guess_category(task_data.get("title", ""))
        row = [
            task_id,
            task_data.get("title", ""),
            category,
            task_data.get("assignee", "Me"),
            task_data.get("due", ""),
            task_data.get("priority", "MED"),
            "Pending",
            date.today().isoformat(),
            task_data.get("notes", ""),
        ]
        ws.append_row(row)
        return task_id
    except Exception as e:
        logger.error(f"write_my_task failed: {e}")
        return False


def mark_done(task_id: str) -> bool:
    try:
        ws   = _get_sheet("My Tasks")
        cell = ws.find(task_id)
        if cell:
            ws.update_cell(cell.row, COL["status"], "Done")
            return True
        return False
    except Exception as e:
        logger.error(f"mark_done failed: {e}")
        return False


def update_task(task_id: str, field: str, value: str) -> bool:
    try:
        ws  = _get_sheet("My Tasks")
        col = FIELD_COL.get(field.lower())
        if not col:
            return False
        cell = ws.find(task_id)
        if not cell:
            return False
        ws.update_cell(cell.row, col, value)
        return True
    except Exception as e:
        logger.error(f"update_task failed: {e}")
        return False
