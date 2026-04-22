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
    n = len(points)
    if n < 5000: step = max(1, n // 1000)
    elif n < 20000: step = max(1, n // 500)
    else: step = max(1, n // 200)
    return points[::step] if step > 1 else points

def slim_format_points(all_paths):
    day_strings = []
    for path in all_paths:
        if path:
            pts_str = ";".join([f"{round(p[0], 6)},{round(p[1], 6)}" for p in path])
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

# 🚩 修正箇所1：セパレーター"|"で分解して、日ごとのリストのリストを返す
def load_latest_route_from_sheet():
    if sheet_total is None: return [], None
    try:
        all_data = sheet_total.get_all_values()
        if len(all_data) < 2: return [], None
        date_row = all_data[0]
        for col_idx in range(len(date_row) - 1, -1, -1):
            val = date_row[col_idx].strip()
            if val:
                all_paths_reconstructed = []
                # "|" で日ごとにバラす
                segments = all_data[1][col_idx].split("|")
                for seg in segments:
                    day_pts = []
                    for coord in seg.split(";"):
                        if coord.strip():
                            day_pts.append(list(map(float, coord.split(","))))
                    if day_pts:
                        all_paths_reconstructed.append(day_pts)
                return all_paths_reconstructed, datetime.strptime(val, '%Y/%m/%d')
        return [], None
    except: return [], None

# 🚩 修正箇所2：読み込んだ「リストのリスト」を正しくセッションに格納
if sheet_total is not None and not st.session_state.loaded_paths:
    paths_list, d = load_latest_route_from_sheet()
    if paths_list:
        st.session_state.loaded_paths = paths_list
        # 統計用にフラットな地点リストも作る
        st.session_state.loaded_points = [p for path in paths_list for p in path]
        st.session_state.loaded_date = d

# ========== データ処理 ==========
all_paths = st.session_state.loaded_paths.copy()
all_combined = st.session_state.loaded_points.copy()

uploaded_files = st.file_uploader("新しいFITファイルを選択して連結してください", type=["fit"], accept_multiple_files=True)
if uploaded_files:
    file_info = []
    for f in uploaded_files:
        try:
            raw_bytes = f.getvalue()
            fit_data = io.BytesIO(raw_bytes)
            fit = FitFile(fit_data)
            first_msg = next(fit.get_messages('record'))
            first_ts = first_msg.get_values().get('timestamp')
            file_info.append((first_ts, raw_bytes))
        except Exception as e:
            file_info.append((f.name, f.getvalue()))
    
    file_info.sort(key=lambda x: x[0])

    if file_info:
        tokyo_tz = pytz.timezone('Asia/Tokyo')
        latest_ts = file_info[-1][0]
        if isinstance(latest_ts, datetime):
            st.session_state.total_save_date = latest_ts.replace(tzinfo=pytz.utc).astimezone(tokyo_tz).date()

    for ts, raw_b in file_info:
        fit = FitFile(io.BytesIO(raw_b))
        file_pts = []
        for r in fit.get_messages('record'):
            v = r.get_values()
            if 'position_lat' in v:
                file_pts.append([v['position_long'] * (180.0/2**31), v['position_lat'] * (180.0/2**31)])
        if file_pts:
            smp = adaptive_sample_points(file_pts)
            all_paths.append(smp)
            all_combined.extend(smp)

# ========== 軌跡表示セクション ==========
st.subheader("🗺️ 軌跡表示")

if all_combined:
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("📍 ロード済み", len(st.session_state.loaded_points))
    with c2: 
        new_pts = len(all_combined) - len(st.session_state.loaded_points)
        st.metric("🆕 新規地点", new_pts)
    with c3: st.metric("📊 合計地点", len(all_combined))

    lats = [p[1] for p in all_combined]; lons = [p[0] for p in all_combined]
    clat, clon, zoom = calculate_auto_zoom(lats, lons)

    # 🚩 修正箇所3：[all_paths]ではなく、すでにリストのリストであるall_pathsをそのまま使う
    layers = [pdk.Layer('PathLayer', pd.DataFrame({'path': [p for p in all_paths if p]}),
                        get_path='path', get_color=[255, 0, 0, 255], get_width=10, width_min_pixels=3)]
    
    st_p, en_p = all_combined[0], all_combined[-1]
    layers.append(pdk.Layer('ScatterplotLayer', pd.DataFrame([
        {'lon': st_p[0], 'lat': st_p[1], 'color': [0, 204, 0]},
        {'lon': en_p[0], 'lat': en_p[1], 'color': [255, 0, 0]}
    ]), get_position=['lon', 'lat'], get_radius=800, get_fill_color='color', radiusMinPixels=8))

    if st.session_state.occupy_mode:
        poly = create_occupy_polygon(all_combined)
        if poly: layers.append(pdk.Layer('PolygonLayer', pd.DataFrame({'polygon': [[poly]]}),
                                        get_polygon='polygon', get_fill_color=[255, 102, 102, 100], filled=True))

    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=clat, longitude=clon, zoom=zoom),
                             map_style='light'), use_container_width=True, key=f"map-{len(all_combined)}")
else:
    st.info("📥 クラウドからロード待機中、またはFITをアップロードしてください。")

st.divider()
st.subheader("### 🎮 Occupy Project")

if not st.session_state.occupy_mode:
    if st.button("🚩 Occupy Tokyo Bay! (このエリアを占拠する)", use_container_width=True):
        st.session_state.occupy_mode = True
        st.balloons()
        st.rerun()
else:
    st.success("✨ **占拠中...** ✨")
    if st.button("🔄 占拠を解除", use_container_width=True):
        st.session_state.occupy_mode = False
        st.rerun()

st.divider()
st.subheader("💾 クラウドに保存")
if all_combined:
    st.caption(f"📍 {len(all_combined)} 地点をスプレッドシートに保存します")
    
    col_date, col_btn = st.columns([3, 1])
    with col_date:
        update_date = st.date_input("保存日付を選択", value=st.session_state.total_save_date)
    
    with col_btn:
        st.write("") 
        st.write("")
        if st.button("📤 更新", use_container_width=True):
            try:
                h = sheet_total.row_values(1)
                num_existing_cols = len(h)
                sheet_total.add_cols(1)
                target_col = num_existing_cols + 1
                
                d_str = update_date.strftime('%Y/%m/%d')
                slim = slim_format_points(all_paths)
                
                sheet_total.update_cell(1, target_col, d_str)
                sheet_total.update_cell(2, target_col, slim)
                
                st.balloons(); st.success(f"✅ {d_str} を保存完了！")
            except Exception as e:
                st.error(f"保存失敗: {e}")