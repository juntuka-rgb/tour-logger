import streamlit as st
import os
import common

# 1. ページ設定（必ず一番最初に配置）
st.set_page_config(page_title="Tour Navigator Portal", layout="wide")

# --- 合言葉（パスワード）チェック機能 ---
def check_password():
    """パスワードが正しいかチェックし、結果をセッション状態に保存する"""
    def password_entered():
        # st.secrets の "APP_PASSWORD" を参照します
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # セキュリティのため入力を削除
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # 初回表示
        st.text_input("合言葉を入力してください", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        # パスワード間違い時
        st.text_input("合言葉が違います。再入力してください", type="password", on_change=password_entered, key="password")
        return False
    return True

# --- 合言葉が正しい場合のみ、以下のメイン処理が実行される ---
if check_password():
    st.title("🚴‍♂️ Tour Navigator: 旅の司令塔")

    # セッション状態の初期化
    if 'fit_coordinates' not in st.session_state:
        st.session_state['fit_coordinates'] = None

    # 2. サイドバーで機能を選択
    menu = st.sidebar.selectbox(
        "機能を選択してください",
        ["実績ログ記録 (Tour Logger)", "累計走行ルート", "ルート計画 (Route Builder)"]
    )

    # 3. 各機能の呼び出し
    if menu == "実績ログ記録 (Tour Logger)":
        st.subheader("📝 本日の実績ログ記録")
        # app_daily_log.py を実行
        if os.path.exists("app_daily_log.py"):
            with open("app_daily_log.py", encoding="utf-8") as f:
                exec(f.read(), globals())
        else:
            st.error("app_daily_log.py が見つかりません。")

    elif menu == "累計走行ルート":
        st.subheader("🗺️ 累計走行ルート")
        # app_total_route.py を実行
        if os.path.exists("app_total_route.py"):
            with open("app_total_route.py", encoding="utf-8") as f:
                exec(f.read())
        else:
            st.error("app_total_route.py が見つかりません。")

    elif menu == "ルート計画 (Route Builder)":
        st.header("📍 ルート計画 (Route Builder)")
        st.info("すでに稼働中のWEBアプリ版を別タブで開きます。")
        
        target_url = "https://japan-tour-navi-cqdzc6bozulghin2drrffx.streamlit.app/"
        
        st.markdown(f"""
        <div style="text-align: center; margin-top: 20px;">
            <a href="{target_url}" target="_blank" style="text-decoration: none;">
                <div style="
                    display: inline-block;
                    background-color: #FF4B4B;
                    color: white;
                    padding: 18px 35px;
                    text-align: center;
                    border-radius: 12px;
                    font-size: 22px;
                    font-weight: bold;
                    cursor: pointer;
                    box-shadow: 0 6px 10px rgba(0,0,0,0.2);
                    transition: 0.3s;
                ">
                    🚀 ルート・ビルダーを起動する
                </div>
            </a>
        </div>
        """, unsafe_allow_html=True)
        
        st.write("")
        st.info("※クリックすると新しいタブでルート・ビルダーが開きます。")

    # スプレッドシートの最新データを参照
    try:
        ss = common.connect()
        sheet_tour = ss.worksheet("旅の記録")
        sheet_total = ss.worksheet("全行程CSV")
    except Exception as e:
        st.error(f"スプレッドシート接続エラー: {e}")