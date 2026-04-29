import streamlit as st
import pydeck as pdk
from fitparse import FitFile
import pandas as pd
import common
import gspread.utils
from datetime import datetime
import pytz
import math
import io
import json

# ========== セッション状態初期化 ==========
if 'loaded_paths' not in st.session_state:
    st.session_state.loaded_paths = []
if 'loaded_points' not in st.session_state:
    st.session_state.loaded_points = []
if 'loaded_date' not in st.session_state:
    st.session_state.loaded_date = None
if 'occupy_mode' not in st.session_state:
    st.session_state.occupy_mode = False
if 'total_save_date' not in st.session_state:
    st.session_state.total_save_date = datetime.now(pytz.timezone('Asia/Tokyo')).date()
if 'view_state' not in st.session_state:
    st.session_state.view_state = {'latitude': 35.68, 'longitude': 139.76, 'zoom': 10}

# ========== スプレッドシート接続 ==========
try:
    ss = common.connect()
    sheet_total = ss.worksheet("全行程CSV")
except:
    st.error("❌ スプレッドシート接続エラー")
    sheet_total = None

# ========== 共通ロジック ==========
def create_occupy_polygon(points):
    if not points or len(points) < 3: return None
    points_sorted = sorted(set(map(tuple, points)))
    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for p in points_sorted:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0: lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(points_sorted):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0: upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]

def adaptive_sample_points(points):
    if not points: return []
    n = len(points)
    if n < 5000: step = max(1, n // 1000)
    elif n < 20000: step = max(1, n // 500)
    else: step = max(1, n // 200)
    return points[::step] if step > 1 else points

def slim_format_points(all_paths):
    day_strings = []
    for path in all_paths:
        if path:
            # 5万文字制限対策：保存時に小数点を5桁に丸めてダイエット
            pts_str = ";".join([f"{round(p[0], 5)},{round(p[1], 5)}" for p in path])
            day_strings.append(pts_str)
    return "|".join(day_strings)

def calculate_auto_zoom(latitudes, longitudes):
    if not latitudes or not longitudes: return 35.68, 139.76, 10
    min_lat, max_lat = min(latitudes), max(latitudes)
    min_lon, max_lon = min(longitudes), max(longitudes)
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2
    max_diff = max(max_lat - min_lat, max_lon - min_lon)
    if max_diff == 0: zoom = 13
    elif max_diff < 0.01: zoom = 15
    elif max_diff < 0.02: zoom = 14
    elif max_diff < 0.05: zoom = 13
    elif max_diff < 0.1: zoom = 12
    elif max_diff < 0.2: zoom = 11
    elif max_diff < 0.5: zoom = 10
    else: zoom = 9
    return center_lat, center_lon, zoom

# 🚩 初期表示：累計と当日データの読み込み
def load_routes():
    cumulative_paths = []
    today_path = []

    # 累計データ（青い線）を読み込む
    try:
        last_col = len(sheet_total.row_values(1))
        if last_col > 0:
            cumulative_data = sheet_total.cell(2, last_col).value
            if cumulative_data:
                cumulative_paths = [
                    [[float(coord.split(",")[0]), float(coord.split(",")[1])] for coord in segment.split(";") if "," in coord]
                    for segment in cumulative_data.split("|") if segment.strip()
                ]
    except Exception as e:
        st.warning(f"累計データの読み込み中にエラーが発生しました: {e}")

    # 当日データ（赤い線）を読み込む
    try:
        sheet_tour = ss.worksheet("旅の記録")
        t_last_col = len(sheet_tour.row_values(1))
        if t_last_col > 0:
            today_data_json = sheet_tour.cell(12, t_last_col).value
            if today_data_json:
                today_data = json.loads(today_data_json)
                today_path = [[lon, lat] for lon, lat in zip(today_data['longitudes'], today_data['latitudes'])]
    except Exception as e:
        st.warning(f"当日データの読み込み中にエラーが発生しました: {e}")

    return cumulative_paths, today_path

# 初期データ実行
cumulative_paths, today_path = load_routes()

# ========== 地図の描画 ==========
def render_map(cumulative_paths, today_path):
    layers = []
    all_coords = []

    # 累計データ（青い線）
    if cumulative_paths:
        layers.append(pdk.Layer(
            "PathLayer",
            data=pd.DataFrame({'path': cumulative_paths}),
            get_path='path',
            get_color=[0, 0, 255, 200],
            width_min_pixels=3
        ))
        for p in cumulative_paths: all_coords.extend(p)

    # 当日データ（赤い線）
    if today_path:
        layers.append(pdk.Layer(
            "PathLayer",
            data=pd.DataFrame({'path': [today_path]}),
            get_path='path',
            get_color=[255, 0, 0, 255],
            get_width=5,
            width_min_pixels=3
        ))
        all_coords.extend(today_path)

    # ズームの自動調整
    if all_coords:
        lats = [c[1] for c in all_coords]; lons = [c[0] for c in all_coords]
        clat, clon, zoom = calculate_auto_zoom(lats, lons)
        st.session_state.view_state = {'latitude': clat, 'longitude': clon, 'zoom': zoom}

    st.pydeck_chart(pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(
            latitude=st.session_state.view_state['latitude'],
            longitude=st.session_state.view_state['longitude'],
            zoom=st.session_state.view_state['zoom']
        ),
        map_style='light'
    ))

render_map(cumulative_paths, today_path)

# ========== 手動連結：FITファイルアップロード ==========
uploaded_files = st.file_uploader("新しいFITファイルを選択して連結してください", type=["fit"], accept_multiple_files=True)
if uploaded_files:
    all_manual = []
    for f in uploaded_files:
        try:
            fit = FitFile(io.BytesIO(f.getvalue()))
            file_pts = []
            for r in fit.get_messages('record'):
                v = r.get_values()
                if 'position_lat' in v:
                    file_pts.append([v['position_long'] * (180.0/2**31), v['position_lat'] * (180.0/2**31)])
            if file_pts:
                all_manual.extend(file_pts)
        except Exception as e:
            st.error(f"FITファイルの処理中にエラーが発生しました: {e}")

    if all_manual:
        # 手動アップロード時は、赤い線を上書き
        today_path = adaptive_sample_points(all_manual)
        st.info("💡 手動アップロードされたデータで当日分を上書きしました。")
        render_map(cumulative_paths, today_path)

# ========== 保存：クラウドに保存 ==========
st.divider()
if st.button("📤 更新", use_container_width=True):
    if not today_path and not cumulative_paths:
        st.error("保存するデータがありません。")
    else:
        try:
            with st.spinner("データを軽量化してスプレッドシートへ保存中..."):
                target_col = len(sheet_total.row_values(1)) + 1
                
                # 1. 保存直前にすべてのパスを「間引き」ロジックに通す
                thinned_cumulative = [adaptive_sample_points(p) for p in cumulative_paths]
                thinned_today = adaptive_sample_points(today_path)
                
                # 2. 連結
                final_paths = thinned_cumulative + ([thinned_today] if thinned_today else [])
                
                # 3. 文字列化（ここで小数点5桁への丸めが実行されます）
                combined_data = slim_format_points(final_paths)

                # 4. 5万文字制限の最終チェック
                if len(combined_data) > 49000:
                    st.error(f"❌ データ量が多すぎます ({len(combined_data)}文字)。これ以上の保存は分割が必要です。")
                else:
                    save_date = datetime.now(pytz.timezone('Asia/Tokyo')).strftime('%Y/%m/%d')
                    sheet_total.add_cols(1)
                    sheet_total.update_cell(1, target_col, save_date)
                    sheet_total.update_cell(2, target_col, combined_data)
                    
                    st.success(f"✅ {save_date} のデータを {target_col}列目に保存しました！")
                    st.balloons()
        except Exception as e:
            st.error(f"保存中にエラーが発生しました: {e}")