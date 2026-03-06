import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_NEW_BOT_TOKEN")
MANAGER_CHAT_ID    = int(os.getenv("MANAGER_CHAT_ID", "0"))
CLAUDE_API_KEY     = os.getenv("CLAUDE_API_KEY", "")
CLAUDE_MODEL       = "claude-sonnet-4-20250514"
GOOGLE_SHEET_ID    = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
DAILY_BRIEF_HOUR   = int(os.getenv("DAILY_BRIEF_HOUR", "9"))
TIMEZONE           = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")
