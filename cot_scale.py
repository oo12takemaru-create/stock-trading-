# -*- coding: utf-8 -*-
"""投機筋ポジションの「10年の物差し」→ docs/cot_scale.json

■ 何のためか
  「日本円のネットが1週間で +103,023枚 動いた」と書いても、その数字が大きいのか
  普通なのか読者には分からない。過去10年の中での位置を出すと、同じ数字が意味を持つ。
  これは**動きの大きさを測る物差し**であって、方向を当てるものではない。
  「当たっているか」は cot_score.py（答え合わせ）が別に測っている。役割が違うので分けている。

■ 既存の cot_fetch.py / cot_score.py は無変更。読みもしない（独立して動く）

■ 市場名が2022年2月に変わっている商品がある
  S&P500・ナスダック・WTIは新旧の名前を両方取らないと2022年2月以降しか返らない。
  cot_score.py で実際に踏んで239週しか出なかったので、ここでも新旧を足して繋ぐ。

■ URLは urllib.parse.urlencode で組むこと
  $where に空白が入るため、素の文字列結合だと
  InvalidURL: URL can't contain control characters で落ちる。

■ 注釈は「公知の事実だけ」
  上位の週に何が起きていたかが出ると価値が上がるが、推測で書くと嘘になる。
  (1) crash_replay.json の歴代暴落日と機械的に突き合わせる（恣意性ゼロ）
  (2) 手入力は下の NOTES だけ。1行・出所をコメントに残す・相場観は書かない
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
HERE = Path(__file__).parent
CRASH = HERE / "docs" / "crash_replay.json"
OUT = HERE / "docs" / "cot_scale.json"

SINCE = "2015-01-01"
TOP_N = 5

# (key, 表示名, CFTCの市場名プレフィックス候補[新旧すべて])
SPECS = [
    ("jpy",    "日本円",              ["JAPANESE YEN - CHICAGO"]),
    ("nikkei", "日経平均先物(円建て)", ["NIKKEI STOCK AVERAGE YEN DENOM"]),
    ("sp500",  "S&P500 (E-mini)",     ["E-MINI S&P 500 - CHICAGO",
                                       "E-MINI S&P 500 STOCK INDEX - CHICAGO"]),
    ("nasdaq", "ナスダック100 (mini)", ["NASDAQ MINI - CHICAGO",
                                       "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO"]),
    ("gold",   "金",                  ["GOLD - COMMODITY EXCHANGE"]),
    ("wti",    "WTI原油",             ["CRUDE OIL, LIGHT SWEET-WTI - ICE",
                                       "CRUDE OIL, LIGHT SWEET - NEW YORK"]),
]

# 手入力の注釈。公知の事実だけ・1行・出所を必ず添える。推測や相場観は書かない。
# 日付はCFTCの報告日（火曜）。
NOTES = {
    # 2026-08-04週: 日米協調介入（財務省・FRBの発表として公表された事実）
    "2026-08-04": "日米協調介入があった週",
}


def fetch_weeks(prefixes):
    """10年分の週次データを古い順で返す。市場名が途中で変わる商品は新旧を日付でまとめる。

    ■ 1つでも取れなければ None を返す（半端な集計を出さないため）
      実際にナスダックの新名だけタイムアウトし、旧名の370週だけで
      「最新=2022-02-01」という誤った結果が出た。取れた分で計算してはいけない。
    """
    merged = {}
    for p in prefixes:
        rows = None
        for attempt in range(3):
            params = {
                "$where": (f"market_and_exchange_names like '{p}%' "
                           f"AND report_date_as_yyyy_mm_dd > '{SINCE}'"),
                "$order": "report_date_as_yyyy_mm_dd ASC",
                "$limit": "1200",
                "$select": ("report_date_as_yyyy_mm_dd,"
                            "noncomm_positions_long_all,noncomm_positions_short_all"),
            }
            url = API + "?" + urllib.parse.urlencode(params)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "kaburadar/1.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    rows = json.load(r)
                break
            except Exception as e:
                print(f"  取得失敗({attempt+1}/3) {p}: {e}", file=sys.stderr)
                time.sleep(4 * (attempt + 1))
        if rows is None:
            return None
        for x in rows:
            d = x["report_date_as_yyyy_mm_dd"][:10]
            try:
                net = int(x["noncomm_positions_long_all"]) - int(x["noncomm_positions_short_all"])
            except (TypeError, ValueError, KeyError):
                continue
            merged.setdefault(d, net)
    return [(d, merged[d]) for d in sorted(merged)]


def crash_notes():
    """歴代の大暴落日を、その日を含むCFTCの報告週（火曜）に割り当てる。
    CFTCの週は水曜〜翌火曜なので、暴落日以降で最初の火曜がその週になる"""
    out = {}
    if not CRASH.exists():
        return out
    try:
        d = json.loads(CRASH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  crash_replay.json を読めない: {e}", file=sys.stderr)
        return out
    for w in d.get("worst_days", []):
        try:
            day = datetime.strptime(w["d"], "%Y-%m-%d").date()
        except Exception:
            continue
        tue = day + timedelta(days=(1 - day.weekday()) % 7)   # その日以降で最初の火曜
        out[tue.isoformat()] = f"日経平均が1日で{w['pct']}%下げた週"
    return out


def pctile(vals, v):
    """下から何パーセンタイルか（v以下の割合）"""
    if not vals:
        return None
    return round(100.0 * sum(1 for x in vals if x <= v) / len(vals), 1)


def main():
    auto = crash_notes()
    notes = dict(auto)
    notes.update(NOTES)          # 手入力が優先
    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "CFTC Commitments of Traders（legacy・先物のみ・non-commercial）",
        "since": SINCE,
        "headline": "その動きは10年で何番目か",
        "caveats": [
            "これは動きの大きさを測る物差しです。方向を当てるものではありません",
            "上位に並ぶのは相場が大きく動いた週です。そのあとどうなったかは別の話です",
            "CFTCは火曜時点の建玉を金曜夕（日本時間の土曜）に公表します。4日遅れです",
            f"母数は{SINCE[:4]}年以降の週次データです。それ以前は含みません",
            "注釈は日経平均の歴代下落日との機械的な突き合わせと、公知の事実のみです",
        ],
        "items": [],
    }

    today = datetime.now(JST).date()
    for key, label, prefixes in SPECS:
        recs = fetch_weeks(prefixes)
        if recs is None or len(recs) < 60:
            n = 0 if recs is None else len(recs)
            print(f"{label}: 取得できず（{n}週）。この商品は出さない", file=sys.stderr)
            out["items"].append({"key": key, "label": label, "note": "データを取得できず"})
            continue
        # 最新週が古すぎるときも出さない。片方の名前だけ取れて
        # 「最新=2022-02-01」のような誤った物差しを出すのを防ぐ
        age = (today - datetime.strptime(recs[-1][0], "%Y-%m-%d").date()).days
        if age > 20:
            print(f"{label}: 最新が{recs[-1][0]}で{age}日前。古すぎるので出さない", file=sys.stderr)
            out["items"].append({"key": key, "label": label, "note": "データを取得できず"})
            continue

        nets = [n for _, n in recs]
        chgs = [(recs[i][0], recs[i][1] - recs[i - 1][1]) for i in range(1, len(recs))]
        cur_date, cur_net = recs[-1]
        cur_chg = chgs[-1][1]

        # 変化幅は「絶対値」で並べる。方向ではなく大きさの物差しなので
        order = sorted(chgs, key=lambda x: -abs(x[1]))
        rank = next(i + 1 for i, (d, _) in enumerate(order) if d == cur_date)

        item = {
            "key": key,
            "label": label,
            "weeks": {"n": len(recs), "from": recs[0][0], "to": recs[-1][0]},
            "latest": {"date": cur_date, "net": cur_net, "change": cur_chg},
            "chg_rank": {"rank": rank, "of": len(chgs)},
            "chg_top": [{"date": d, "change": v, "note": notes.get(d)}
                        for d, v in order[:TOP_N]],
            "level_pct": pctile(nets, cur_net),
            "level_range": {"min": min(nets), "max": max(nets),
                            "min_date": recs[nets.index(min(nets))][0],
                            "max_date": recs[nets.index(max(nets))][0]},
            "note_latest": notes.get(cur_date),
        }
        out["items"].append(item)
        print(f"{label:22s} {len(recs)}週 最新{cur_date} net{cur_net:+,} "
              f"変化{cur_chg:+,} → {len(chgs)}週中{rank}位 / 水準{item['level_pct']}%タイル")

    if not any("weeks" in i for i in out["items"]):
        print("全商品で取得に失敗", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"\n→ {OUT.name} ({OUT.stat().st_size/1024:.1f}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
