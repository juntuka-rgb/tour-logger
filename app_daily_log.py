import streamlit as st
import pydeck as pdk
from fitparse import FitFile
import pandas as pd
import plotly.graph_objects as go
import math
from datetime import timedelta, datetime
import pytz
import time
import common
import gspread.utils
import json
import io

# ========== セッション状態の初期化 ==========
if 'animation_playing' not in st.session_state:
    st.session_state.animation_playing = False
if 'current_index' not in st.session_state:
    st.session_state.current_index = 0
if 'previous_index' not in st.session_state:
    st.session_state.previous_index = 0
if 'peak_indices' not in st.session_state:
    st.session_state.peak_indices = []
if 'current_peak_num' not in st.session_state:
    st.session_state.current_peak_num = 0
if 'view_state' not in st.session_state:
    st.session_state.view_state = {'latitude': 35.6762, 'longitude': 139.6503, 'zoom': 13}
if 'last_uploaded_file_name' not in st.session_state:
    st.session_state.last_uploaded_file_name = None
if 'distance_km' not in st.session_state:
    st.session_state.distance_km = 0.0
# 🚩 FITから取得した日付を保持するセッション
if 'selected_date' not in st.session_state:
    st.session_state.selected_date = datetime.now(pytz.timezone('Asia/Tokyo')).date()

# ========== スプレッドシート接続 (旅の記録のみに限定) ==========
try:
    ss = common.connect()
    sheet_tour = ss.worksheet("旅の記録")
except Exception as e:
    st.error(f"❌ スプレッドシート接続エラー: {e}")
    sheet_tour = None

# ========== 完璧だった地図・解析ロジック ==========

def extract_data_from_fit(uploaded_file):
    fitfile = FitFile(io.BytesIO(uploaded_file.getvalue()))  # Use BytesIO to avoid file closure issues
    altitudes = []; timestamps = []; latitudes = []; longitudes = []
    for record in fitfile.get_messages('record'):
        data = record.get_values()
        if 'altitude' in data and 'position_lat' in data and 'position_long' in data and 'timestamp' in data:
            altitudes.append(data['altitude'])
            latitudes.append(data['position_lat'] * (180 / 2**31))
            longitudes.append(data['position_long'] * (180 / 2**31))
            timestamps.append(data['timestamp'])

    # Save coordinates to st.session_state
    st.session_state['fit_coordinates'] = {
        'latitudes': latitudes,
        'longitudes': longitudes,
        'altitudes': altitudes
    }

    return timestamps, altitudes, latitudes, longitudes

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad = math.radians(lat1); lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2); lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad; dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    return R * 2 * math.asin(math.sqrt(a))

def calculate_distance(latitudes, longitudes):
    distance = 0.0
    for i in range(len(latitudes) - 1):
        distance += haversine(latitudes[i], longitudes[i], latitudes[i+1], longitudes[i+1])
    return distance

def calculate_elevation_gain(altitudes, window=180):
    if len(altitudes) < window:
        return 0.0

    # 1. 窓幅を180（約3分間）に少し戻します。
    # 250だと平坦化しすぎていた微細な「本物の登り」を救い出します。
    s = pd.Series(altitudes)
    smoothed = s.rolling(window=window, min_periods=1, center=True).mean()
    
    # 2. サンプリング間隔を10秒に1点へ（これも少し密度を上げます）
    thinned_altitudes = smoothed.iloc[::10].tolist()

    gain = 0.0
    if not thinned_altitudes:
        return 0.0

    last_fixed_alt = thinned_altitudes[0]
    # 3. 閾値を「15.0メートル」に緩和。
    # 18mでは弾かれていた「そこそこの坂」をカウント対象に戻します。
    threshold = 15.0 

    for current_alt in thinned_altitudes:
        diff = current_alt - last_fixed_alt
        if diff >= threshold:
            gain += diff
            last_fixed_alt = current_alt
        elif current_alt < (last_fixed_alt - 5.0):
            # 下落時の基準点更新も5mに緩和。
            # これにより、次の上昇を捉えやすくします。
            last_fixed_alt = current_alt

    return int(gain)
            
def calculate_movement_and_rest_time(timestamps, latitudes, longitudes):
    total_time = timestamps[-1] - timestamps[0]
    total_seconds = total_time.total_seconds()
    rest_seconds = 0
    for i in range(len(timestamps) - 1):
        time_diff = (timestamps[i+1] - timestamps[i]).total_seconds()
        dist = haversine(latitudes[i], longitudes[i], latitudes[i+1], longitudes[i+1])
        speed = (dist / (time_diff / 3600)) if time_diff > 0 else 0
        if speed < 0.5 and time_diff >= 300: rest_seconds += time_diff
    return total_seconds - rest_seconds, rest_seconds

def detect_peaks(altitudes, window_size=5, min_height_diff=15):
    peak_indices = []
    for i in range(window_size, len(altitudes) - window_size):
        window_max = max(altitudes[i-window_size:i+window_size+1])
        if altitudes[i] == window_max:
            surrounding = altitudes[i-window_size:i] + altitudes[i+1:i+window_size+1]
            if altitudes[i] - (sum(surrounding)/len(surrounding)) >= min_height_diff:
                peak_indices.append(i)
    return peak_indices

def calculate_auto_zoom(latitudes, longitudes):
    if not latitudes: return 35.6762, 139.6503, 13
    center_lat = sum(latitudes) / len(latitudes); center_lon = sum(longitudes) / len(longitudes)
    max_range = max(max(latitudes)-min(latitudes), max(longitudes)-min(longitudes))
    if max_range == 0: zoom = 13
    elif max_range < 0.01: zoom = 15
    elif max_range < 0.02: zoom = 14
    elif max_range < 0.05: zoom = 13
    elif max_range < 0.1: zoom = 12
    elif max_range < 0.2: zoom = 11
    elif max_range < 0.5: zoom = 10
    else: zoom = 9
    return center_lat, center_lon, zoom

def create_map_with_current_position(latitudes, longitudes, current_index, view_state):
    route_path = [[longitudes[i], latitudes[i]] for i in range(len(latitudes))]
    path_layer = pdk.Layer('PathLayer', pd.DataFrame({'path': [route_path]}), get_path='path', get_color=[65, 105, 225, 200], get_width=3, widthMinPixels=2)
    markers = [{'lat': latitudes[0], 'lon': longitudes[0], 'type': 'start'}, {'lat': latitudes[-1], 'lon': longitudes[-1], 'type': 'goal'}]
    if 0 <= current_index < len(latitudes): markers.append({'lat': latitudes[current_index], 'lon': longitudes[current_index], 'type': 'current'})
    df = pd.DataFrame(markers)
    df['color'] = df['type'].map({'start':[0,204,0,255], 'goal':[255,0,0,255], 'current':[255,165,0,255]})
    marker_layer = pdk.Layer('ScatterplotLayer', df, get_position=['lon', 'lat'], get_radius=500, get_fill_color='color', radiusMinPixels=8)
    return pdk.Deck(layers=[path_layer, marker_layer], initial_view_state=pdk.ViewState(longitude=view_state['longitude'], latitude=view_state['latitude'], zoom=view_state['zoom']), map_provider='carto', map_style='light', height=300)

def create_altitude_chart_with_marker(distances, altitudes, current_index):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=distances, y=altitudes, mode='lines', line=dict(color='#4169E1', width=3), fill='tozeroy', fillcolor='rgba(65, 105, 225, 0.2)'))
    if 0 <= current_index < len(altitudes):
        fig.add_trace(go.Scatter(x=[distances[current_index]], y=[altitudes[current_index]], mode='markers', marker=dict(size=8, color='#FF6600')))
    fig.update_layout(
        height=80,
        margin=dict(l=30, r=10, t=0, b=20),
        plot_bgcolor='rgba(245, 245, 245, 0.5)',
        paper_bgcolor='white',
        showlegend=False,
        xaxis=dict(tickfont=dict(color='black')),  # X軸の文字色を黒に設定
        yaxis=dict(tickfont=dict(color='black'))   # Y軸の文字色を黒に設定
    )
    return fig

def adaptive_sample_points(points):
    n = len(points)
    if n < 5000: step = max(1, n // 1000)
    elif n < 20000: step = max(1, n // 500)
    else: step = max(1, n // 200)
    return points[::step] if step > 1 else points

# ========== 表示メイン処理 ==========
uploaded_file = st.file_uploader("FITファイルを選択してください", type=["fit"])
if uploaded_file:
    ts, alts, lats, lons = extract_data_from_fit(uploaded_file)
    if alts:
        # 🚩 閃き：FITのタイムスタンプから日付を自動取得してセッションに保存
        if ts:
            tokyo_tz = pytz.timezone('Asia/Tokyo')
            fit_date = ts[0].replace(tzinfo=pytz.utc).astimezone(tokyo_tz).date()
            st.session_state.selected_date = fit_date

        st.session_state.distance_km = calculate_distance(lats, lons)
        st.session_state.elevation_gain = calculate_elevation_gain(alts)  # 獲得標高を計算してセッションに保存

        if uploaded_file.name != st.session_state.last_uploaded_file_name:
            st.session_state.last_uploaded_file_name = uploaded_file.name
            st.session_state.current_index = 0
            clat, clon, zoom = calculate_auto_zoom(lats, lons)
            st.session_state.view_state = {'latitude': clat, 'longitude': clon, 'zoom': zoom}

        st.subheader("🗺️ 移動軌跡地図", divider="blue")
        st.pydeck_chart(create_map_with_current_position(lats, lons, st.session_state.current_index, st.session_state.view_state))

        # 累積距離リストの作成
        distances = [0.0]
        for i in range(1, len(lats)):
            distances.append(distances[-1] + haversine(lats[i-1], lons[i-1], lats[i], lons[i]))

        # 高低差グラフの表示
        st.subheader("⛰️ 高低差グラフ", divider="green")
        fig = create_altitude_chart_with_marker(distances, alts, st.session_state.current_index)
        st.plotly_chart(fig, use_container_width=True)

# ========== 収支入力フォーム (旅の記録のみに書き込む) ==========
st.divider()
st.subheader("💰 旅の収支入力", divider="green")

with st.sidebar:
    # 🚩 二桁数字から「カレンダー選択」に変更。初期値はFITの日付と連動
    final_date = st.date_input("旅の日付を選択", value=st.session_state.selected_date)

col1, col2, col3 = st.columns(3)
with col1:
    accommodation = st.text_input("宿泊地")
    sleep_time = st.text_input("睡眠時間 (hh:mm)")
    calories = st.number_input("消費カロリー (kcal)", value=0)  # 消費カロリーを追加
with col2:
    cycling_distance = st.number_input("走行距離 (km)", value=st.session_state.distance_km, format="%.1f")
    elevation_gain = st.number_input("獲得標高 (m)", value=int(st.session_state.get('elevation_gain', 0)))  # 獲得標高を追加
with col3:
    acc_fee = st.number_input("宿泊費＆入浴料等", value=0); food = st.number_input("食費", value=0)
    travel = st.number_input("旅費交通費", value=0); maintenance = st.number_input("自転車維持費", value=0); misc = st.number_input("雑費", value=0)

note = st.text_area("メモ")

# ========== ボタン押下時にスプレッドシートへ記録 ==========
if st.button("🚀 旅ログをスプレッドシートに反映", use_container_width=True):
    if sheet_tour is None:
        st.error("❌ スプレッドシートに接続されていません。")
    else:
        with st.spinner("新規列を作成して反映中..."):
            try:
                first_row = sheet_tour.row_values(1)
                next_col = max(len(first_row) + 1, 3)

                # 必要に応じて列を拡張
                if next_col > sheet_tour.col_count:
                    sheet_tour.add_cols(next_col - sheet_tour.col_count)

                # 再計算して列を確認
                next_col = max(len(sheet_tour.row_values(1)) + 1, 3)

                # 保存する日付をカレンダーの選択値から取得
                save_date_str = final_date.strftime('%Y/%m/%d')

                updates = [
                    {'range': gspread.utils.rowcol_to_a1(1, next_col), 'values': [[save_date_str]]},
                    {'range': gspread.utils.rowcol_to_a1(2, next_col), 'values': [[sleep_time]]},
                    {'range': gspread.utils.rowcol_to_a1(4, next_col), 'values': [[cycling_distance]]},
                    {'range': gspread.utils.rowcol_to_a1(5, next_col), 'values': [[accommodation]]},
                    {'range': gspread.utils.rowcol_to_a1(6, next_col), 'values': [[acc_fee]]},
                    {'range': gspread.utils.rowcol_to_a1(7, next_col), 'values': [[food]]},
                    {'range': gspread.utils.rowcol_to_a1(8, next_col), 'values': [[travel]]},
                    {'range': gspread.utils.rowcol_to_a1(9, next_col), 'values': [[maintenance]]},
                    {'range': gspread.utils.rowcol_to_a1(10, next_col), 'values': [[misc]]},
                    {'range': gspread.utils.rowcol_to_a1(11, next_col), 'values': [[note]]}
                ]

                if ':' in sleep_time:
                    h, m = map(int, sleep_time.split(':')[:2])
                    updates.append({'range': gspread.utils.rowcol_to_a1(3, next_col), 'values': [[h * 60 + m]]})

                # FITデータを12行目に追加（軽量化）
                if 'fit_coordinates' in st.session_state:
                    fit_data = st.session_state['fit_coordinates']
                    thinned_points = adaptive_sample_points(list(zip(fit_data['latitudes'], fit_data['longitudes'])))
                    thinned_data = {
                        'latitudes': [p[0] for p in thinned_points],
                        'longitudes': [p[1] for p in thinned_points],
                        'altitudes': fit_data['altitudes'][:len(thinned_points)]
                    }
                    updates.append({'range': gspread.utils.rowcol_to_a1(12, next_col), 'values': [[json.dumps(thinned_data)]]})

                    # デバッグログを追加
                    st.info(f"✅ FITデータを {next_col}列目の12行目に書き込みました。")

                # 獲得標高と消費カロリーを追加
                updates.append({'range': gspread.utils.rowcol_to_a1(13, next_col), 'values': [[elevation_gain]]})
                updates.append({'range': gspread.utils.rowcol_to_a1(14, next_col), 'values': [[calories]]})

                sheet_tour.batch_update(updates, value_input_option='USER_ENTERED')

                col_alphabet = gspread.utils.rowcol_to_a1(1, next_col).replace("1", "")
                st.success(f"✅ {save_date_str} のデータを {col_alphabet}列目に保存しました！")
                st.balloons()

            except Exception as e:
                st.error(f"❌ 反映失敗: {e}")

# Remove redundant button logic