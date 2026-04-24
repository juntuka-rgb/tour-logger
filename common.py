import gspread
from google.oauth2.service_account import Credentials
import streamlit as st  # これを追加

def connect():
    """Google Sheets への接続"""
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    # 修正ポイント：ファイルからではなく、Secrets（辞書形式）から読み込む
    # ⚠️ Secrets 側のキー名が "gcp_service_account" であると想定しています
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope
    )
    client = gspread.authorize(creds)
    
    # スプレッドシート「月次処理データ_2026」に接続
    ss = client.open("月次処理データ_2026")
    return ss
