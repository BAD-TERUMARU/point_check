#!/usr/bin/env python3
"""
麻雀ポイント推移トラッカー
使い方: streamlit run app.py
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from amae_api import MODES, extract_player_result, get_player_records, get_player_stats, search_player

PARTICIPANTS_FILE = Path("data/participants.json")

st.set_page_config(
    page_title="麻雀PT推移トラッカー",
    page_icon="🀄",
    layout="wide",
)


# ── 認証 ─────────────────────────────────────────────────────

def check_auth():
    if st.session_state.get("authenticated"):
        return

    st.title("🀄 麻雀PT推移トラッカー")
    st.subheader("ログイン")

    with st.form("login_form"):
        pw = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン")

    if submitted:
        correct = st.secrets.get("password", "mahjong")
        if pw == correct:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("パスワードが正しくありません")

    st.stop()


# ── 参加者リスト管理 ──────────────────────────────────────────

def load_participants() -> list[dict]:
    if PARTICIPANTS_FILE.exists():
        return json.loads(PARTICIPANTS_FILE.read_text(encoding="utf-8"))
    return []


def save_participants(participants: list[dict]):
    PARTICIPANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PARTICIPANTS_FILE.write_text(
        json.dumps(participants, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── API キャッシュ ────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_records(player_id: int, mode: int, start_ts: int, end_ts: int) -> list[dict]:
    return get_player_records(player_id, mode, start_ts, end_ts)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_stats(player_id: int, mode: int, start_ts: int, end_ts: int) -> dict:
    try:
        return get_player_stats(player_id, mode, start_ts, end_ts)
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def cached_search(name: str) -> list[dict]:
    return search_player(name, limit=10)


# ── データ変換 ────────────────────────────────────────────────

def build_player_df(player_id: int, mode: int,
                    start_ts: int, end_ts: int) -> tuple[pd.DataFrame, dict]:
    """
    ゲーム記録を DataFrame に変換し、統計情報も返す
    Returns: (df, stats)
    """
    records = fetch_records(player_id, mode, start_ts, end_ts)
    stats   = fetch_stats(player_id, mode, start_ts, end_ts)
    rows = []
    for rec in records:
        result = extract_player_result(rec, player_id)
        if result:
            rows.append(result)

    empty_df = pd.DataFrame(
        columns=["date", "start_time", "score", "grading_score", "rank", "absolute_pt"]
    )
    if not rows:
        return empty_df, stats

    df = pd.DataFrame(rows)
    df["date"] = (
        pd.to_datetime(df["start_time"], unit="s")
        .dt.tz_localize("UTC")
        .dt.tz_convert("Asia/Tokyo")
        .dt.date
    )
    df = df.sort_values("start_time").reset_index(drop=True)

    # 絶対PT: player_stats の level.score (期間末の段位ポイント) を基準に逆算
    level_info = stats.get("level", {})
    end_score  = level_info.get("score", 0) if isinstance(level_info, dict) else 0

    gs     = df["grading_score"].values.astype(float)
    cumsum = np.cumsum(gs)
    # pt_after_game_i = end_score - sum(gs[i+1:]) = end_score - total + cumsum[i]
    df["absolute_pt"] = (end_score - gs.sum() + cumsum).astype(int)

    return df, stats


# ── サイドバー: 参加者管理 ────────────────────────────────────

def sidebar_participants():
    st.sidebar.header("参加者管理")

    participants = load_participants()

    # 追加
    with st.sidebar.expander("➕ 参加者を追加"):
        search_name = st.text_input("プレーヤー名で検索", key="search_name")
        if search_name:
            try:
                results = cached_search(search_name)
                if results:
                    options = {
                        f"{r['nickname']} (ID: {r['id']})": r for r in results
                    }
                    selected_key = st.selectbox("検索結果", list(options.keys()))
                    display_name = st.text_input(
                        "表示名", value=options[selected_key]["nickname"]
                    )
                    if st.button("追加"):
                        r = options[selected_key]
                        existing_ids = {p["player_id"] for p in participants}
                        if r["id"] in existing_ids:
                            st.warning("すでに追加済みです")
                        else:
                            participants.append({
                                "display_name": display_name,
                                "player_id": r["id"],
                                "nickname": r["nickname"],
                            })
                            save_participants(participants)
                            st.success(f"{display_name} を追加しました")
                            st.cache_data.clear()
                            st.rerun()
                else:
                    st.info("該当プレーヤーが見つかりません")
            except requests.RequestException as e:
                st.error(f"API エラー: {e}")

    # 削除
    if participants:
        with st.sidebar.expander("🗑️ 参加者を削除"):
            names = [p["display_name"] for p in participants]
            to_remove = st.selectbox("削除する参加者", names, key="remove_select")
            if st.button("削除", key="remove_btn"):
                participants = [p for p in participants if p["display_name"] != to_remove]
                save_participants(participants)
                st.success(f"{to_remove} を削除しました")
                st.rerun()

    return participants


# ── メイン ────────────────────────────────────────────────────

def main():
    check_auth()

    st.title("🀄 麻雀PT推移トラッカー")

    participants = sidebar_participants()

    if not participants:
        st.info("サイドバーから参加者を追加してください。")
        return

    # ── フィルター ──
    today = date.today()
    default_start = today - timedelta(days=30)

    col1, col2, col3 = st.columns([2, 2, 3])

    with col1:
        mode_name = st.selectbox("ルーム", list(MODES.keys()), index=0)
        mode_id = MODES[mode_name]

    with col2:
        date_range = st.date_input(
            "期間",
            value=(
                st.session_state.get("date_start", default_start),
                st.session_state.get("date_end",   today),
            ),
            max_value=today,
        )
        btn_c1, btn_c2, btn_c3 = st.columns(3)
        if btn_c1.button("今日"):
            st.session_state["date_start"] = today
            st.session_state["date_end"]   = today
            st.rerun()
        if btn_c2.button("今月"):
            st.session_state["date_start"] = date(today.year, today.month, 1)
            st.session_state["date_end"]   = today
            st.rerun()
        if btn_c3.button("今年"):
            st.session_state["date_start"] = date(today.year, 1, 1)
            st.session_state["date_end"]   = today
            st.rerun()

    with col3:
        all_names = [p["display_name"] for p in participants]
        selected_names = st.multiselect(
            "表示する参加者 (空=全員)",
            all_names,
            default=[],
        )
        if not selected_names:
            selected_names = all_names

    if len(date_range) != 2:
        st.warning("期間を開始日〜終了日で指定してください")
        return

    start_date, end_date = date_range
    start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    end_ts   = int(datetime.combine(end_date,   datetime.max.time()).timestamp())

    # ── データ取得 ──
    player_dfs:   dict[str, pd.DataFrame] = {}
    player_stats: dict[str, dict]         = {}
    filtered = [p for p in participants if p["display_name"] in selected_names]

    progress = st.progress(0, text="データ取得中...")
    for i, p in enumerate(filtered):
        progress.progress((i + 1) / len(filtered), text=f"{p['display_name']} を取得中...")
        try:
            df, stats = build_player_df(p["player_id"], mode_id, start_ts, end_ts)
            player_dfs[p["display_name"]]   = df
            player_stats[p["display_name"]] = stats
        except Exception as e:
            st.warning(f"{p['display_name']}: 取得失敗 ({e})")
    progress.empty()

    if not player_dfs:
        st.info("データがありません。")
        return

    # ── PT推移グラフ (1日1点: その日の最終対局後の絶対PT) ──
    st.subheader(f"📈 PT推移 ({mode_name} / {start_date} 〜 {end_date})")

    fig = go.Figure()
    for name, df in player_dfs.items():
        if df.empty:
            continue
        # 1日1点: その日の最後の対局後の絶対PT
        daily = df.groupby("date")["absolute_pt"].last().reset_index()
        dates = daily["date"].tolist()
        pts   = daily["absolute_pt"].tolist()

        # 開始アンカー: 期間開始日に対局がない場合のみ先頭に追加
        first_data_date = daily["date"].iloc[0]
        if hasattr(first_data_date, "date"):
            first_data_date = first_data_date.date()
        start_pt = int(pts[0] - df[df["date"] == daily["date"].iloc[0]]["grading_score"].sum())
        if first_data_date != start_date:
            dates = [start_date] + dates
            pts   = [start_pt]   + pts

        fig.add_trace(go.Scatter(
            x=dates, y=pts,
            mode="lines+markers",
            name=name,
            line=dict(width=2),
            marker=dict(size=7),
            hovertemplate="%{x}<br>PT: %{y:,d}<extra>" + name + "</extra>",
        ))

    fig.update_layout(
        xaxis_title="日付",
        yaxis_title="段位ポイント",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=450,
        margin=dict(l=0, r=0, t=30, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── サマリーテーブル (rank_rates は API から直接取得) ──
    st.subheader("📊 期間サマリー")

    summary_rows = []
    for name, df in player_dfs.items():
        stats = player_stats.get(name, {})
        level_info = stats.get("level", {})
        current_pt = level_info.get("score", 0) if isinstance(level_info, dict) else 0
        rank_rates = stats.get("rank_rates", [])
        count      = stats.get("count", 0)

        if not rank_rates or df.empty:
            summary_rows.append({
                "名前": name, "対局数": count or len(df),
                "現在PT": current_pt, "総PT変動": 0,
                "1位率(%)": 0.0, "4位率(%)": 0.0, "平均着順": 0.0,
            })
            continue

        total_pt   = int(df["grading_score"].sum())
        rank1_rate = round(rank_rates[0] * 100, 1) if len(rank_rates) > 0 else 0.0
        rank4_rate = round(rank_rates[3] * 100, 1) if len(rank_rates) > 3 else 0.0
        avg_rank   = round(sum((i+1)*r for i, r in enumerate(rank_rates)), 2)

        summary_rows.append({
            "名前":     name,
            "対局数":   count,
            "現在PT":   current_pt,
            "総PT変動": total_pt,
            "1位率(%)": rank1_rate,
            "4位率(%)": rank4_rate,
            "平均着順": avg_rank,
        })

    summary_df = pd.DataFrame(summary_rows).set_index("名前")

    def highlight_top(row):
        max_pt = summary_df["総PT変動"].max()
        if row["総PT変動"] == max_pt and max_pt > 0:
            return ["background-color: #2a6e2a; font-weight: bold"] * len(row)
        return [""] * len(row)

    styled = summary_df.style.apply(highlight_top, axis=1).format({
        "現在PT":   "{:,d}",
        "総PT変動": "{:+d}",
        "1位率(%)": "{:.1f}%",
        "4位率(%)": "{:.1f}%",
        "平均着順": "{:.2f}",
    })
    st.dataframe(styled, use_container_width=True)

    # ── 詳細ゲームログ ──
    with st.expander("📋 ゲームログ詳細"):
        tab_names = list(player_dfs.keys())
        if tab_names:
            tabs = st.tabs(tab_names)
            for tab, (name, df) in zip(tabs, player_dfs.items()):
                with tab:
                    if df.empty:
                        st.info("対局記録なし")
                        continue
                    display_df = df.copy()
                    # 日時 (日付 + 時刻)
                    display_df["日時"] = (
                        pd.to_datetime(display_df["start_time"], unit="s")
                        .dt.tz_localize("UTC")
                        .dt.tz_convert("Asia/Tokyo")
                        .dt.strftime("%Y-%m-%d %H:%M")
                    )
                    display_df = display_df[["日時", "rank", "score", "grading_score", "absolute_pt"]].copy()
                    display_df.columns = ["日時", "着順", "スコア", "PT変動", "段位PT"]
                    display_df = display_df.sort_values("日時", ascending=False)
                    st.dataframe(display_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
