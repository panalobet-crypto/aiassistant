"""
sheets.py — Google Sheets for Personal Task Assistant
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
    # 如果 tab 不存在就自动创建
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=10)
        # 添加 header
        ws.append_row(["Task ID", "Title", "Due Date", "Priority", "Status", "Created", "Notes"])
        return ws


def get_my_tasks(status_filter="pending") -> List[Dict]:
    """
    获取个人任务
    status_filter: "pending" = 未完成, "all" = 全部, "done" = 已完成
    """
    try:
        ws = _get_sheet("My Tasks")
        records = ws.get_all_records()
        if status_filter == "pending":
            return [r for r in records if str(r.get("Status", "")).strip().lower() != "done" and r.get("Task ID")]
        elif status_filter == "done":
            return [r for r in records if str(r.get("Status", "")).strip().lower() == "done"]
        else:
            return [r for r in records if r.get("Task ID")]
    except Exception as e:
        logger.error(f"get_my_tasks failed: {e}")
        return []


def get_tasks_due_today() -> List[Dict]:
    """获取今天到期的任务"""
    try:
        today = date.today().isoformat()
        tasks = get_my_tasks(status_filter="pending")
        return [t for t in tasks if str(t.get("Due Date", "")).strip() <= today and str(t.get("Due Date", "")).strip()]
    except Exception as e:
        logger.error(f"get_tasks_due_today failed: {e}")
        return []


def get_tasks_this_week() -> List[Dict]:
    """获取本周任务（今天到本周日）"""
    try:
        today = date.today()
        week_end = today + timedelta(days=(6 - today.weekday()))
        tasks = get_my_tasks(status_filter="all")
        return [
            t for t in tasks
            if str(t.get("Due Date", "")).strip() and
            today.isoformat() <= str(t.get("Due Date", "")).strip() <= week_end.isoformat()
        ]
    except Exception as e:
        logger.error(f"get_tasks_this_week failed: {e}")
        return []


def write_my_task(task_data: Dict) -> bool:
    """写入新任务"""
    try:
        ws = _get_sheet("My Tasks")
        all_rows = ws.get_all_values()
        # 生成任务 ID
        task_num = len([r for r in all_rows if r and r[0].startswith("P")]) + 1
        task_id = f"P{task_num:03d}"
        today = date.today().isoformat()
        row = [
            task_id,
            task_data.get("title", ""),
            task_data.get("due", ""),
            task_data.get("priority", "MED"),
            "Pending",
            today,
            task_data.get("notes", ""),
        ]
        ws.append_row(row)
        logger.info(f"任务已记录: {task_id} — {task_data.get('title')}")
        return True
    except Exception as e:
        logger.error(f"write_my_task failed: {e}")
        return False


def mark_done(task_id: str) -> bool:
    """标记任务为完成"""
    try:
        ws = _get_sheet("My Tasks")
        cell = ws.find(task_id)
        if cell:
            ws.update_cell(cell.row, 5, "Done")  # Status 列
            return True
        return False
    except Exception as e:
        logger.error(f"mark_done failed: {e}")
        return False
