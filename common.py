import gspread
from google.oauth2.service_account import Credentials

def connect():
    """Google Sheets への接続"""
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    creds = Credentials.from_service_account_file(
        "sheets-key.json", scopes=scope
    )
    client = gspread.authorize(creds)
    
    # スプレッドシート「月次処理データ_2026」に接続
    ss = client.open("月次処理データ_2026")
    return ss

def clean_num(value):
    """値を数値（float）に安全に変換"""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
