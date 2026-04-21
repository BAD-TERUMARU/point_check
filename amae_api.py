#!/usr/bin/env python3
"""
amae-koromo API ラッパー
https://ak-data-1.sapk.ch/api/v2/pl4/
"""

import time
import requests

BASE_URL = "https://ak-data-1.sapk.ch/api/v2/pl4"

MODES = {
    "玉": 12,
    "玉東": 11,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PointTracker/1.0)",
    "Origin": "https://amae-koromo.sapk.ch",
    "Referer": "https://amae-koromo.sapk.ch/",
}


def get_player_stats(player_id: int, mode: int, start_ts: int, end_ts: int) -> dict:
    """期間内の統計 (rank_rates, level.score 等)"""
    url = f"{BASE_URL}/player_stats/{player_id}/{start_ts}/{end_ts}?mode={mode}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def search_player(name: str, limit: int = 10) -> list[dict]:
    """プレーヤー名でID検索"""
    url = f"{BASE_URL}/search_player/{name}?limit={limit}&tag=all"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_player_records(player_id: int, mode: int,
                       start_ts: int, end_ts: int) -> list[dict]:
    """
    指定期間のゲーム記録を全件取得 (ページネーション対応)

    Parameters
    ----------
    player_id : int
    mode      : int  (11=玉東, 12=玉)
    start_ts  : int  Unix timestamp (開始)
    end_ts    : int  Unix timestamp (終了)
    """
    all_records: list[dict] = []
    cursor = end_ts
    limit = 100

    while True:
        url = (
            f"{BASE_URL}/player_records/{player_id}/{cursor}/{start_ts}"
            f"?limit={limit}&mode={mode}&descending=true"
        )
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        records = resp.json()

        if not records:
            break

        all_records.extend(records)

        if len(records) < limit:
            break

        oldest_ts = min(r.get("startTime") or r.get("start_time", 0) for r in records)
        if oldest_ts <= start_ts:
            break
        cursor = oldest_ts - 1
        time.sleep(0.3)

    return all_records


def extract_player_result(record: dict, player_id: int) -> dict | None:
    """
    ゲーム記録から特定プレーヤーの結果を抽出

    Returns
    -------
    {
      "start_time": int,
      "score":       int,   # 最終スコア (例: 51300)
      "grading_score": int, # PT変動 (例: +152, -20)
      "rank":        int,   # 着順 1-4
    }
    or None (該当プレーヤーが記録にいない場合)
    """
    players: list[dict] = record.get("players", [])

    # accountId で該当プレーヤーを探す
    target = None
    for p in players:
        aid = p.get("accountId") or p.get("account_id")
        if aid == player_id:
            target = p
            break

    if target is None:
        return None

    # 着順: スコア降順で並べたときの順位
    scores = sorted([p.get("score", 0) for p in players], reverse=True)
    rank = scores.index(target.get("score", 0)) + 1

    grading = target.get("gradingScore") or target.get("grading_score") or 0

    # APIはキャメルケース (startTime) で返す
    start_time = record.get("startTime") or record.get("start_time") or 0

    return {
        "start_time":    start_time,
        "score":         target.get("score", 0),
        "grading_score": grading,
        "rank":          rank,
    }
