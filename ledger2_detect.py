# -*- coding: utf-8 -*-
"""出来事の台帳（v2）の検出関数 — 1種類＝1関数（検証台帳 設計書 v1 段階1・2026-11-18）

■ 足し方
  新しい出来事を足すときは、このファイルに `@detector(...)` を付けた関数を1つ書くだけ。
  関数は detect_xxx(date, data) → [ev(...), ...] の形で、
    date … その情報が公開された日（"YYYY-MM-DD"）
    data … Sources（下）。その日に読めたファイルだけを読む
  を受け取り、その日の出来事を返す。記録（ledger2.py）・答え合わせ（ledger2_score.py）は共通。
  遡及（段階3）でも同じ関数を日付を変えて呼べる（data の置き場を差し替える）。

■ 時点を守る
  - 「今の値」しか持たないファイル（prices.json・karauri.json など）は、中の日付が date と
    一致するときだけ使う。一致しなければ何も返さない（別の日の値で判定しない）。
  - 日付つきで残っている履歴（stophigh_hist・tdnet_hist・shinyo_daily・fc_revision・buyback・
    splits・radar_history・gauge・シグナル台帳）は、その日の行だけを読む。

■ いつ知り得たか（known）
  各出来事に known を持たせ、答え合わせの起点（基準価格の日）を決める。
    "pre_open"    … 寄り付き前（〜08:59）に公開 → その日（営業日なら）の始値
    "intraday"    … 立会時間中に公開 → 翌営業日の始値（日足しか無いので控えめな側に倒す）
    "after_close" … 引け後に公開 → 翌営業日の始値
  開示は公開時刻から決める（time_known()）。

■ しきい値は設計書 v1 の仮の値のまま。変えるときは DETECTOR_VERSION を上げ、別の出来事として扱う。

■ 言葉
  出力の文面には事実だけを書く。売買を勧める言い方・先行きを言う言い方は使わない。
"""
import csv
import json
import re
import unicodedata
from datetime import date as _date
from pathlib import Path

from jp_bizday import is_bizday, next_bizday

HERE = Path(__file__).resolve().parent
DETECTOR_VERSION = "1.0"

# ──────────────────────────────────────── しきい値（設計書 v1 の仮の値）
MARGIN_BUY_UP = 0.30          # 信用買残の前回比 +30% 以上
SHORT_UP = 0.30               # 空売り残高割合の前回比 +30% 以上（設計書は「前回比」とだけ。信用残と同じ +30% を仮に置く）
SHORT_THRESH = 0.5            # 空売り残高の報告義務の下限（%）
VOLUME_SURGE = 3.0            # 出来高が前20日平均の3倍以上
GAP_PCT = 5.0                 # 寄り付きが前日終値比 ±5% 以上
HIGH52_EPS = 0.0              # prices.json の hi52（終値÷250営業日の高値−1・小数1桁）が 0 以上
MARGIN_RATIO_HIDE = 100.0     # 信用倍率 100倍超・売残0 はサイトに数字を出さない（統括 2026-11-05）
GAUGE_STAGES = ((4, 2, "prepare"), (3, 1, "watch"), (0, 0, "calm"))   # 傾斜計: 灯った数 → 段階（gauge_fetch.py と同じ）

LIVE_START = "2026-11-18"     # v2 の当日記録を始めた日。これより前の日は live として書かない

# ── 株価の汚れの守り（2026-11-21・px_sanity）
# 当日記録の検出は、Yahoo の1年分から作った値（prices.json の 52週高値・出来高の倍率、stophigh_hist、data/bars）を読む。
# その銘柄の日足（data/bars_backfill ＋ data/bars を ledger2_panel で1つにした表）に、px_sanity の検査で
# 異常（上場前の仮の値・再上場前の値・分割の調整のずれ・極端な分割の記録・単発の桁とび）か捨てた日が、
# その出来事の判定に使う期間（下の暦日）の中にある銘柄は、その日のその種類を記録しない。異常の無い銘柄は今までと同じ。
# 既存のシグナル台帳の取り込み（signal_*）は「サイトがその日に出したもの」の記録なので飛ばさず、
# 答え合わせ（ledger2_score.py）の側で「データの異常」の invalid を足す。
DIRTY_LOOKBACK = {            # 判定に使う期間（暦日）。ここに無い種類は飛ばさない
    "high52_close": 366, "low52_close": 366, "ytd_high_close": 366, "ytd_low_close": 366,
    "volume_surge": 31,
    "gap_up": 5, "gap_down": 5, "stop_high": 5, "stop_low": 5, "stop_high_touch": 5, "stop_low_touch": 5,
}
DIRTY_LOOKBACK_SIGNAL = {     # 取り込み（signal_*）の答え合わせで invalid にする期間。入力が 52週高値なら1年
    "signal_treasure_contra": 366, "signal_treasure_quiet": 31,
}
DIRTY_LOOKBACK_SIGNAL_DEFAULT = 5
CHART_LOOKBACK = {"from_high52": 366, "dev_ma25": 40, "above_ma25": 40, "ret20": 31, "vol_ratio20": 31}

REGISTRY = []                 # [(name, types, func, version, desc)]
TYPES = {}                    # event_type → {label, rule, known, source, version}


def detector(types, version=DETECTOR_VERSION):
    """検出関数の登録。types = {event_type: (表示名, 判定の規則, 知り得た時点, データ源)}"""
    def wrap(fn):
        REGISTRY.append((fn.__name__, tuple(types), fn, version))
        for k, (label, rule, known, src) in types.items():
            TYPES[k] = {"label": label, "rule": rule, "known": known, "source": src, "version": version}
        return fn
    return wrap


def ev(event_type, code, params=None, snapshot=None, time=None, known="after_close", src=None):
    """検出関数が返す1件（event_id・context などは ledger2.py が足す）"""
    return {"event_type": event_type, "code": code4(code), "time": time, "known": known,
            "event_params": params or {}, "snapshot": snapshot or {}, "src": src}


# ──────────────────────────────────────── 小道具
def code4(c):
    c = str(c or "").strip()
    return c[:4] if len(c) == 5 and c.endswith("0") else c


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def jsonl(p):
    out = []
    try:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out


def time_known(d, hhmm):
    """公開日と時刻から known を決める。時刻が無い・休日なら after_close（控えめな側）"""
    if not hhmm or not re.fullmatch(r"\d{1,2}:\d{2}", str(hhmm)):
        return "after_close"
    h, m = (int(x) for x in hhmm.split(":"))
    if not is_bizday(_date.fromisoformat(d)):
        return "after_close"
    if (h, m) < (9, 0):
        return "pre_open"
    if (h, m) < (15, 30):
        return "intraday"
    return "after_close"


def base_date(d, known):
    """答え合わせの起点（基準価格＝この日の始値）"""
    if known == "pre_open" and is_bizday(_date.fromisoformat(d)):
        return d
    return next_bizday(d)


def nfkc_norm(t):
    return re.sub(r"[\s　]+", "", unicodedata.normalize("NFKC", t or ""))


# ──────────────────────────────────────── 開示の表題の分類（サイト側 gen_kaiji.classify と同じ規則）
# サイト側を変えたらここも合わせる（tests/test_ledger2.py に同じ例を置いてある）
RE_ETF = re.compile(r"ETF|ETN|上場投資信託|日々の開示事項|投資法人|インフラファンド|受益証券")


def classify(title):
    """表題 → [(種類, 小分け)]。kessan/shusei/haito/jisha/bunkatsu/tob/yutai/taiho"""
    t = nfkc_norm(title)
    out = []
    corr = bool(re.match(r"^\(?(訂正|変更|開示事項の変更|開示事項の経過)", t) or "の訂正" in t[:30] or t.startswith("「"))
    if "決算短信" in t and not corr and "訂正" not in t:
        out.append(("kessan", ""))
    if not corr and re.search(r"業績予想.{0,30}修正|上方修正|下方修正|業績の修正", t):
        out.append(("shusei", "上方" if "上方" in t else "下方" if "下方" in t else ""))
    if not corr and re.search(r"配当予想.{0,12}修正|増配|減配|復配|無配|記念配当|特別配当|剰余金の配当", t):
        sub = ("増配" if re.search(r"増配|復配|記念配当|特別配当", t) else "減配" if re.search(r"減配|無配", t) else "")
        out.append(("haito", sub))
    if ("自己株" in t and "取得" in t and re.search(r"決定|決議|に関するお知らせ", t)
            and not re.search(r"処分|消却|取得状況|取得結果|取得終了|取得の終了|取得完了|買付け?結果|変更|訂正|譲渡|給付", t)) \
            or ("自己株" in t and "立会外買付" in t and not re.search(r"結果|終了", t)):
        out.append(("jisha", ""))
    if not corr and ((re.search(r"株式(?:の)?分割(?!に伴う|に伴い|に係る|後の|後における|を踏まえた|の実施に伴う)", t)
                      and not re.search(r"会社分割|吸収分割|新設分割", t)) or re.search(r"株式(?:の)?無償割当", t)):
        out.append(("bunkatsu", "無償割当" if "無償割当" in t else ""))
    if "公開買付" in t and not re.search(r"準ずる行為|買集め|買い集め|への応募|に応募|応募契約|投資利益|特別利益|特別損失|持分法", t):
        out.append(("tob", "MBO" if re.search(r"MBO|マネジメント・?バイアウト", t) else "自社株" if "自己株式の公開買付" in t else ""))
    if "株主優待" in t and re.search(r"新設|導入|再開|変更|拡充|見直し|廃止|休止|中止|内容|一部変更", t) and not re.search(r"発送|到着", t):
        sub = "新設" if re.search(r"新設|導入|再開", t) else "廃止" if re.search(r"廃止|休止|中止", t) else "変更"
        out.append(("yutai", sub))
    if re.search(r"主要株主|筆頭株主|親会社(?:の|及び|および|並びに)?.{0,10}異動|その他の関係会社.{0,10}異動", t) and not corr:
        out.append(("taiho", ""))
    return out


# ──────────────────────────────────────── データの読み口
class Sources:
    """収集側 docs/ などを読む窓口。遡及のときは docs を差し替えて同じ関数を呼ぶ"""

    def __init__(self, docs=None, root=None, bars=None):
        self.root = Path(root) if root else HERE
        self.docs = Path(docs) if docs else self.root / "docs"
        self.bars_dir = Path(bars) if bars else self.root / "data" / "bars"
        self._c = {}

    def _memo(self, key, fn):
        if key not in self._c:
            try:
                self._c[key] = fn()
            except Exception as e:                 # 1つのファイルが壊れても他は動く
                print(f"::warning::{key} が読めない: {e}")
                self._c[key] = None
        return self._c[key]

    # 今の値だけのファイル（中の日付を必ず確かめて使う）
    def prices(self, d):
        p = self._memo("prices", lambda: jload(self.docs / "prices.json"))
        return p if p and p.get("trade_date") == d else None

    def karauri(self, d):
        k = self._memo("karauri", lambda: jload(self.docs / "karauri.json"))
        return k if k and k.get("report_date") == d else None

    def thermo_now(self):
        return self._memo("thermo100", lambda: jload(self.docs / "thermo100.json"))

    def gauge(self):
        return self._memo("gauge", lambda: jload(self.docs / "gauge.json"))

    # 日付つきの履歴
    def karauri_events(self):
        return self._memo("karauri_events", lambda: jload(self.root / "karauri_events" / "events.json", []))

    def stophigh(self, d):
        return [x for x in self._memo(f"stophigh{d[:7]}", lambda: jsonl(self.docs / "stophigh_hist" / f"{d[:7]}.jsonl")) or []
                if x.get("d") == d]

    def tdnet(self, d):
        j = self._memo(f"tdnet{d}", lambda: jload(self.docs / "tdnet_hist" / f"{d}.json"))
        if not j:
            return []
        cols = j.get("cols") or []
        return [dict(zip(cols, r)) for r in j.get("rows") or []]

    def fc_revision(self, d):
        """{id: item} と {(code4, ti): item}（その月のファイル）"""
        def load():
            items = (jload(self.docs / "fc_revision" / f"{d[:7]}.json", {}) or {}).get("items") or {}
            return items
        return self._memo(f"fcrev{d[:7]}", load) or {}

    def buyback(self):
        return self._memo("buyback", lambda: (jload(self.docs / "buyback.json", {}) or {}).get("items") or [])

    def splits(self):
        return self._memo("splits", lambda: jload(self.docs / "splits.json", {}) or {})

    def shinyo_days(self):
        """{申込日: {code: [売残, 売残の前日比, 買残, 買残の前日比]}} と {申込日: 公表日}"""
        def load():
            days = {}
            for f in sorted((self.docs / "shinyo_daily").glob("????-??.json")):
                days.update((jload(f, {}) or {}).get("days") or {})
            pub = (jload(self.docs / "shinyo_daily.json", {}) or {}).get("published") or {}
            return days, pub
        return self._memo("shinyo_days", load) or ({}, {})

    def kisei_days(self):
        """data/kisei/*.jsonl（kisei_fetch.py・1営業日1行）→ {日付: 行}。同じ日が2行あれば先の行"""
        def load():
            out = {}
            for f in sorted((self.root / "data" / "kisei").glob("????-??.jsonl")):
                for r in jsonl(f):
                    if r.get("date") and r["date"] not in out:
                        out[r["date"]] = r
            return out
        return self._memo("kisei_days", load) or {}

    def listed_shares(self):
        return self._memo("listed", lambda: (jload(self.docs / "shinyo_listed.json", {}) or {}).get("items") or {})

    def radar_items(self):
        def load():
            import sys
            sys.path.insert(0, str(HERE))
            from radar_published import published_items
            return published_items(self.docs)
        return self._memo("radar", load) or []

    def thermo_hist(self):
        return self._memo("thermo_hist", lambda: {x["d"]: x for x in
                                                  (jload(self.docs / "thermo100_hist.json", {}) or {}).get("items") or []})

    def ledger_rows(self, d):
        return [r for r in self._memo(f"ledger{d[:7]}", lambda: jsonl(self.docs / "ledger" / f"{d[:7]}.jsonl")) or []
                if r.get("date") == d]

    def universe(self):
        def load():
            out = {}
            with open(self.root / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("code"):
                        out[r["code"].strip()] = {"sector33": (r.get("sector33") or "").strip() or None,
                                                  "scale": (r.get("scale") or "").strip() or None,
                                                  "market": (r.get("market") or "").strip() or None}
            return out
        return self._memo("universe", load) or {}

    def stock_codes(self, d):
        """内国株の範囲（ETF・REIT・TPM を除く）= jpx_listed_codes ∪ tenbagger_universe ∪ その日の prices.json"""
        base = self._memo("stock_codes", lambda: set((jload(self.docs / "jpx_listed_codes.json", {}) or {}).get("codes") or [])
                          | set(self.universe()))
        p = self.prices(d)
        return (base or set()) | set(((p or {}).get("items") or {}).keys())

    def bars(self, d):
        """data/bars/YYYY/YYYY-MM-DD.csv.gz → {code: {open,high,low,close,adj_close,volume}}（無ければ None）"""
        def load():
            import gzip
            p = self.bars_dir / d[:4] / f"{d}.csv.gz"
            if not p.exists():
                return None
            out = {}
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    def fl(k):
                        try:
                            return float(r[k]) if r.get(k) not in (None, "") else None
                        except ValueError:
                            return None
                    out[r["code"]] = {k: fl(k) for k in ("open", "high", "low", "close", "adj_close", "volume")}
            return out
        return self._memo(f"bars{d}", load)

    def hist_stats(self, d):
        """52週安値・年初来高値／安値の判定に使う値（ledger2_panel.Panel.hist_stats）。
        当日記録では data/bars_backfill（過去1年）＋ data/bars（当日記録）から作る。その日の data/bars が無ければ None"""
        if self.bars(d) is None:
            return None

        # 1回だけ作る。値はどれも「その日までの足」だけで計算する（後ろ向きの窓）ので、後の日の足があっても変わらない
        pn = self.panel()
        return pn.hist_stats(d) if pn else None

    # ── 決算の前（pre_earnings・2026-11-19）
    def kessan_now(self):
        """docs/kessan.json（JPX の決算発表予定日・今の版だけ。中の today で日付を確かめて使う）"""
        return self._memo("kessan_now", lambda: jload(self.docs / "kessan.json"))

    def yotei_asof(self, d):
        """d の時点で公表されていた決算発表予定日 {code: 予定日}（d より後の日だけ）。
        data/ledger2/kessan_yotei_asof/ の d の行（pre_earnings.py snapshot が毎日残す）。
        行が無ければ、docs/kessan.json がその日の版（today == d）のときだけそこから作る。どちらも無ければ None"""
        def load(ym):
            out = {}
            for r in jsonl(self.yotei_dir / f"{ym}.jsonl"):
                if r.get("d") and r["d"] not in out:      # 同じ日の行は最初の1行（後から書き換えない）
                    out[r["d"]] = r
            return out
        rec = (self._memo(f"yotei_asof{d[:7]}", lambda: load(d[:7])) or {}).get(d)
        if rec:
            return rec
        return yotei_from_kessan(self.kessan_now(), d)

    @property
    def yotei_dir(self):
        return getattr(self, "_yotei_dir", None) or self.root / "data" / "ledger2" / "kessan_yotei_asof"

    @yotei_dir.setter
    def yotei_dir(self, p):
        self._yotei_dir = Path(p)

    def kessan_dates(self):
        """{code: [(公開日, 時刻), ...]}（決算短信を出した日。新しい順ではなく古い順）。
        docs/kessan_flash_hist/（2026-08-26〜）＋ docs/tdnet_hist/ の表題（kessan）"""
        def load():
            out = {}
            for f in sorted((self.docs / "kessan_flash_hist").glob("????-??.json")):
                for day in (jload(f, {}) or {}).get("days") or []:
                    for x in day.get("items") or []:
                        if x.get("c") and day.get("d"):
                            out.setdefault(code4(x["c"]), set()).add((day["d"], x.get("t") or ""))
            for f in sorted((self.docs / "tdnet_hist").glob("????-??-??.json")):
                j = jload(f, {}) or {}
                cols = j.get("cols") or []
                for r in j.get("rows") or []:
                    row = dict(zip(cols, r))
                    if row.get("c") and any(k == "kessan" for k, _ in classify(row.get("ti") or "")):
                        out.setdefault(code4(row["c"]), set()).add((f.stem, row.get("t") or ""))
            # 同じ日に2つの時刻（flash と tdnet）が入ることがあるので、日ごとに早い時刻1つに
            res = {}
            for c, s in out.items():
                by = {}
                for d0, t0 in s:
                    if d0 not in by or (t0 and (not by[d0] or t0 < by[d0])):
                        by[d0] = t0
                res[c] = sorted(by.items())
            return res
        return self._memo("kessan_dates", load) or {}

    def fc_revision_all(self):
        """docs/fc_revision/*.json の全 items（{id: item}）"""
        def load():
            out = {}
            for f in sorted((self.docs / "fc_revision").glob("????-??.json")):
                out.update((jload(f, {}) or {}).get("items") or {})
            return out
        return self._memo("fcrev_all", load) or {}

    def panel(self):
        """data/bars_backfill（過去1年）＋ data/bars（当日記録）の日足の表（ledger2_panel・px_sanity の検査つき）"""
        def load():
            import ledger2_panel as P
            return P.build(self.root)
        return self._memo("panel", load)

    def px_dirty(self, d, lookback_days):
        """d とその前 lookback_days 暦日に、日足の検査で異常か捨てた日がある銘柄 {code: 状態}（無ければ {}）"""
        pn = self.panel()
        return pn.dirty_codes(d, lookback_days) if pn is not None else {}

    def split_ex(self, code):
        """その銘柄の分割の権利落ち日 {ex: ratio}"""
        s = self.splits()
        out = {}
        for x in (s.get("by_code") or {}).get(code4(code)) or []:
            if x.get("ex") and x.get("ratio"):
                out[x["ex"]] = float(x["ratio"])
        for key in ("upcoming", "recent"):
            for x in s.get(key) or []:
                if code4(x.get("code")) == code4(code) and x.get("ex") and x.get("ratio"):
                    out[x["ex"]] = float(x["ratio"])
        return out


# ════════════════════════════════════════ 需給
@detector({"margin_buy_surge": ("信用買残の急増", f"日々公表の信用買残が前回（前の申込日）比 +{int(MARGIN_BUY_UP*100)}% 以上",
                                "after_close", "JPX 銘柄別信用取引残高（日次）docs/shinyo_daily/")})
def detect_margin_buy_surge(d, data):
    days, pub = data.shinyo_days()
    stocks = data.stock_codes(d)
    out = []
    for apply_d in sorted(days):
        p = pub.get(apply_d) or next_bizday(apply_d)
        if p != d:
            continue
        for c, v in (days[apply_d] or {}).items():
            if not v or len(v) < 4 or v[2] is None or v[3] is None or code4(c) not in stocks:
                continue
            s, sd, b, bd = v
            prev = b - bd
            if prev <= 0 or b < prev * (1 + MARGIN_BUY_UP):
                continue
            ratio = (b / s) if s else None
            snap = {"apply_date": apply_d, "buy": b, "buy_prev": prev, "sell": s,
                    "margin_ratio": round(ratio, 2) if ratio is not None else None}
            if not s or (ratio is not None and ratio > MARGIN_RATIO_HIDE):
                snap["hide_value"] = True            # サイトに数字を出さない（統括 2026-11-05）
            sh = data.listed_shares().get(code4(c))
            out.append(ev("margin_buy_surge", c,
                          {"threshold": MARGIN_BUY_UP, "chg": round(b / prev - 1, 3), "basis": "daily",
                           "listed_pct": round(b / sh * 100, 3) if sh else None},
                          snap, known="after_close", src=f"shinyo_daily/{apply_d[:7]}.json#{apply_d}"))
    return out


def _karauri_moves(d, data):
    """d に公表された空売り残高の動き [(code, 機関, 割合%, 前回%, kind)]。
    karauri.json（その日の版）を優先し、無ければ karauri_events/events.json（週1で追記）"""
    k = data.karauri(d)
    if k:
        return [(m["c"], m["s"], m.get("r"), m.get("pr"), m.get("kind"), m.get("calc")) for m in k.get("moves") or []], "karauri.json"
    rows = []
    for e in data.karauri_events() or []:
        if e.get("pub") != d:
            continue
        r = round((e.get("ratio") or 0) * 100, 2)
        pr = round(e["prev"] * 100, 2) if e.get("prev") is not None else 0
        kind = {"新規": "new" if e.get("prev") is None else "up", "増加": "up"}.get(e.get("event"))
        if kind:
            rows.append((e["code"], e["seller"], r, pr, kind, e.get("calc")))
    return rows, "karauri_events/events.json"


@detector({"short_new_inst": ("空売り残高の新しい機関", "空売り残高の報告に、前回の報告が無い機関が 0.5% 以上で初めて載った",
                              "after_close", "JPX 空売り残高 docs/karauri.json（公表は計算日の2営業日後の夕方）"),
           "short_surge": ("空売り残高の急増", f"機関ごとの空売り残高割合が前回比 +{int(SHORT_UP*100)}% 以上（前回も 0.5% 以上）",
                           "after_close", "同上")})
def detect_short(d, data):
    moves, src = _karauri_moves(d, data)
    stocks = data.stock_codes(d)
    new, up = {}, {}
    for c, s, r, pr, kind, calc in moves:
        if r is None or code4(c) not in stocks:          # ETF・REIT は入れない（jukyu_events と同じ範囲）
            continue
        if kind == "new" and r >= SHORT_THRESH:
            new.setdefault(code4(c), []).append({"inst": s, "ratio_pct": r, "calc_date": calc})
        elif kind == "up" and pr and pr >= SHORT_THRESH and r >= pr * (1 + SHORT_UP):
            up.setdefault(code4(c), []).append({"inst": s, "ratio_pct": r, "prev_pct": pr, "calc_date": calc,
                                                "chg": round(r / pr - 1, 3)})
    out = []
    for c, lst in sorted(new.items()):
        out.append(ev("short_new_inst", c, {"threshold_pct": SHORT_THRESH, "n_inst": len(lst)},
                      {"insts": lst}, known="after_close", src=f"{src}#{d}"))
    for c, lst in sorted(up.items()):
        out.append(ev("short_surge", c, {"threshold": SHORT_UP, "n_inst": len(lst),
                                         "max_chg": max(x["chg"] for x in lst)},
                      {"insts": lst}, known="after_close", src=f"{src}#{d}"))
    return out


# ════════════════════════════════════════ 信用取引の規制（2026-11-20 追加）
# data/kisei/YYYY-MM.jsonl（kisei_fetch.py）の「その日」と「直前の営業日」の一覧を比べる。
# 直前の営業日の行が無い（初日・取れなかった日の次の日）は出来事を作らない（どの日に変わったか分からないため）。
# 銘柄ごとの強さ: 日々公表=1、特別周知=+5、規制（増担保など）=+10＋委託保証金率(%)＋うち現金(%)/10、新規の停止=+1000
KISEI_SRC = "JPX 日々公表銘柄・特別周知銘柄・信用取引に関する規制等（data/kisei/・kisei_fetch.py）"
RE_KISEI_RATE = re.compile(r"委託保証金率を\s*(\d+)\s*[％%]以上(?:（うち現金\s*(\d+)\s*[％%]以上）)?")


def kisei_state(rec):
    """1日分の行 → {code: {nichinichi, tokubetsu, reg_rate, reg_cash, reg_stop, reg_text, strength}}"""
    st = {}

    def one(c):
        return st.setdefault(code4(c), {"nichinichi": False, "tokubetsu": False, "reg_rate": None,
                                        "reg_cash": None, "reg_stop": False, "reg_text": None})
    for r in rec.get("nichinichi") or []:
        one(r["コード"])["nichinichi"] = True
    for r in rec.get("tokubetsu") or []:
        one(r["コード"])["tokubetsu"] = True
    for r in rec.get("kisei") or []:
        s = one(r["コード"])
        txt = r.get("規制の内容") or ""
        s["reg_text"] = txt
        m = RE_KISEI_RATE.search(txt)
        s["reg_rate"] = int(m.group(1)) if m else 0
        s["reg_cash"] = int(m.group(2)) if m and m.group(2) else 0
        s["reg_stop"] = "停止" in txt
    for s in st.values():
        reg = s["reg_text"] is not None
        s["strength"] = ((1 if s["nichinichi"] else 0) + (5 if s["tokubetsu"] else 0)
                         + ((10 + s["reg_rate"] + s["reg_cash"] / 10) if reg else 0) + (1000 if s["reg_stop"] else 0))
    return st


def _prev_bizday(d):
    cur = _date.fromisoformat(d)
    for _ in range(30):
        cur = _date.fromordinal(cur.toordinal() - 1)
        if is_bizday(cur):
            return cur.isoformat()
    return None


def kisei_changes(d, data):
    """d とその直前の営業日の差 → [(kind, code, before, after)]。kind = new/tighten/ease/release"""
    get = getattr(data, "kisei_days", None)
    days = get() if get else {}
    cur, prev = days.get(d), days.get(_prev_bizday(d))
    if not cur or not prev:
        return []
    a, b = kisei_state(prev), kisei_state(cur)
    out = []
    for c in sorted(set(a) | set(b)):
        s0 = (a.get(c) or {}).get("strength", 0)
        s1 = (b.get(c) or {}).get("strength", 0)
        if s0 == s1:
            continue
        kind = "new" if s0 == 0 else "release" if s1 == 0 else "tighten" if s1 > s0 else "ease"
        out.append((kind, c, a.get(c), b.get(c)))
    return out


def _kisei_ev(kind, d, data):
    out = []
    for k, c, s0, s1 in kisei_changes(d, data):
        if k != kind:
            continue
        brief = lambda s: None if not s else {x: s[x] for x in ("nichinichi", "tokubetsu", "reg_rate", "reg_cash", "reg_stop")}
        out.append(ev(f"kisei_{kind}", c, {"from": brief(s0), "to": brief(s1),
                                           "strength_from": (s0 or {}).get("strength", 0),
                                           "strength_to": (s1 or {}).get("strength", 0)},
                      {"reg_text": (s1 or s0 or {}).get("reg_text"), "prev_date": _prev_bizday(d)},
                      known="after_close", src=f"kisei/{d[:7]}.jsonl#{d}"))
    return out


@detector({"kisei_new": ("信用取引の規制: 新規の指定", "前の営業日にどの一覧（日々公表・特別周知・規制）にも無かった銘柄が、その日の一覧に載った",
                         "after_close", KISEI_SRC)})
def detect_kisei_new(d, data):
    return _kisei_ev("new", d, data)


@detector({"kisei_tighten": ("信用取引の規制: 強化", "載っていた銘柄の強さが上がった（日々公表→規制、委託保証金率の引き上げ、特別周知の追加など）",
                             "after_close", KISEI_SRC)})
def detect_kisei_tighten(d, data):
    return _kisei_ev("tighten", d, data)


@detector({"kisei_ease": ("信用取引の規制: 緩和", "一覧には残ったまま、強さが下がった（規制の解除で日々公表だけになった、など）",
                          "after_close", KISEI_SRC)})
def detect_kisei_ease(d, data):
    return _kisei_ev("ease", d, data)


@detector({"kisei_release": ("信用取引の規制: 解除", "前の営業日に一覧にあった銘柄が、その日はどの一覧にも無い",
                             "after_close", KISEI_SRC)})
def detect_kisei_release(d, data):
    return _kisei_ev("release", d, data)


# ════════════════════════════════════════ 値動き
@detector({"stop_high": ("ストップ高（終値）", "終値が制限値幅の上限（stophigh.py の close_high）", "after_close", "docs/stophigh_hist/"),
           "stop_low": ("ストップ安（終値）", "終値が制限値幅の下限（close_low）", "after_close", "docs/stophigh_hist/"),
           "stop_high_touch": ("一時ストップ高", "高値だけが上限に届いた（touch_high）", "after_close", "docs/stophigh_hist/"),
           "stop_low_touch": ("一時ストップ安", "安値だけが下限に届いた（touch_low）", "after_close", "docs/stophigh_hist/")})
def detect_stop(d, data):
    kmap = {"close_high": "stop_high", "close_low": "stop_low", "touch_high": "stop_high_touch", "touch_low": "stop_low_touch"}
    out = []
    for x in data.stophigh(d):
        if x.get("t") != "ev" or x.get("k") not in kmap:
            continue
        out.append(ev(kmap[x["k"]], x["c"], {"kind": x["k"], "chg": x.get("chg"), "vr": x.get("vr"),
                                            "kakudai": bool(x.get("kakudai"))},
                      {k: x.get(k) for k in ("base", "up", "lo", "o", "h", "l", "cl", "val", "t500")},
                      known="after_close", src=f"stophigh_hist/{d[:7]}.jsonl"))
    return out


@detector({"volume_surge": ("出来高の急増", f"当日の出来高が前20日平均の {VOLUME_SURGE:g} 倍以上（prices.json の vr）",
                            "after_close", "docs/prices.json（movers_daily.py）")})
def detect_volume_surge(d, data):
    p = data.prices(d)
    if not p:
        return []
    out = []
    for c, it in (p.get("items") or {}).items():
        if it.get("stale") or it.get("vr") is None or it["vr"] < VOLUME_SURGE:
            continue
        out.append(ev("volume_surge", c, {"threshold": VOLUME_SURGE, "vr": it["vr"]},
                      {"close": it.get("p"), "chg": it.get("c"), "value_mil": it.get("v")},
                      known="after_close", src="prices.json"))
    return out


@detector({"high52_close": ("終値で52週高値", "終値が250営業日の高値以上（prices.json の hi52 が 0 以上・小数1桁で丸めた値）",
                            "after_close", "docs/prices.json")})
def detect_high52(d, data):
    p = data.prices(d)
    if not p:
        return []
    out = []
    for c, it in (p.get("items") or {}).items():
        if it.get("stale") or it.get("hi52") is None or it["hi52"] < HIGH52_EPS:
            continue
        out.append(ev("high52_close", c, {"hi52": it["hi52"]}, {"close": it.get("p"), "chg": it.get("c")},
                      known="after_close", src="prices.json"))
    return out


@detector({"low52_close": ("終値で52週安値", "終値が250営業日の安値以下（安値の最小に対する終値の位置・小数1桁で丸めて 0 以下）。履歴240本以上か、履歴が全部ある（途中で上場した）銘柄だけ",
                           "after_close", "data/bars_backfill ＋ data/bars（ledger2_panel.hist_stats）"),
           "ytd_high_close": ("終値で年初来高値", "終値がその年の高値以上（小数1桁で丸めて 0 以上）。その年の取引日が20日以上ある日だけ",
                              "after_close", "同上"),
           "ytd_low_close": ("終値で年初来安値", "終値がその年の安値以下（小数1桁で丸めて 0 以下）。その年の取引日が20日以上ある日だけ",
                             "after_close", "同上")})
def detect_hist_extremes(d, data):
    """2026-11-18 追加（予兆レーダー 段階3）。52週高値（high52_close）と同じ丸め・同じ「当日を含む」窓で、安値側と年初来を見る"""
    import ledger2_panel as P
    hs = data.hist_stats(d)
    if not hs:
        return []
    stocks = data.stock_codes(d)
    out = []
    for c, x in sorted(hs.items()):
        if c in ("1306", "^N225") or c not in stocks:
            continue
        if x["full"] and x["lo52"] is not None and x["lo52"] <= 0:
            out.append(ev("low52_close", c, {"lo52": x["lo52"], "rows": x["n"]}, {}, known="after_close", src=f"bars/{d}"))
        if x["ytd_n"] >= P.MIN_YTD:
            if x["ytd_hi"] is not None and x["ytd_hi"] >= 0:
                out.append(ev("ytd_high_close", c, {"ytd_hi": x["ytd_hi"], "ytd_rows": x["ytd_n"]}, {},
                              known="after_close", src=f"bars/{d}"))
            if x["ytd_lo"] is not None and x["ytd_lo"] <= 0:
                out.append(ev("ytd_low_close", c, {"ytd_lo": x["ytd_lo"], "ytd_rows": x["ytd_n"]}, {},
                              known="after_close", src=f"bars/{d}"))
    return out


@detector({"gap_up": ("窓を開けて高く寄り付き", f"始値が前日終値比 +{GAP_PCT:g}% 以上", "intraday", "data/bars/（daily_bars.py）"),
           "gap_down": ("窓を開けて安く寄り付き", f"始値が前日終値比 −{GAP_PCT:g}% 以下", "intraday", "data/bars/")})
def detect_gap(d, data):
    """窓は寄り付きの瞬間に分かるが、その始値そのものでは動けないので known=intraday
    （起点＝翌営業日の始値）。分割の権利落ち日は前日終値と比べられないので外す。
    前日の足が data/bars に無いときは、prices.json（その日の版）の前日比から前日終値を逆算する"""
    today = data.bars(d)
    if not today:
        return []
    prev_d = None
    cur = _date.fromisoformat(d)
    from datetime import timedelta
    for i in range(1, 10):
        x = cur - timedelta(days=i)
        if is_bizday(x):
            prev_d = x.isoformat()
            break
    prev = data.bars(prev_d) if prev_d else None
    p = data.prices(d)
    out = []
    for c, b in today.items():
        if c in ("1306", "^N225") or not b.get("open") or not b.get("close"):
            continue
        pc, how = None, None
        if prev and prev.get(c) and prev[c].get("close"):
            pc, how = prev[c]["close"], f"bars/{prev_d}"
        elif p and (p.get("items") or {}).get(c, {}).get("c") is not None:
            it = p["items"][c]
            pc, how = b["close"] / (1 + it["c"] / 100), "prices.json の前日比から逆算"
        if not pc:
            continue
        if d in data.split_ex(c):
            continue
        g = (b["open"] / pc - 1) * 100
        if abs(g) < GAP_PCT:
            continue
        out.append(ev("gap_up" if g > 0 else "gap_down", c, {"threshold_pct": GAP_PCT, "gap_pct": round(g, 2)},
                      {"open": b["open"], "prev_close": round(pc, 2), "prev_close_src": how, "close": b["close"]},
                      known="intraday", src=f"bars/{d}"))
    return out


# ════════════════════════════════════════ 開示（TDnet の表題）
def _rev_dir(fc, row, kind):
    """fc_revision から向き（業績: 上方/下方・配当: 増配/減配）。同じ id → 同じ会社・同じ日の順"""
    it = fc.get(row.get("id"))
    cands = [it] if it else [v for v in fc.values() if v.get("d") and code4(v.get("c")) == code4(row.get("c"))
                                 and v.get("ti") == row.get("ti")]
    for v in cands:
        if not v:
            continue
        if kind == "shusei" and v.get("dir") in ("上方", "下方"):
            return v["dir"], "fc_revision"
        if kind == "haito":
            dv = (v.get("div") or {}).get("dir")
            if dv in ("増配", "減配"):
                return dv, "fc_revision"
    return None, None


@detector({"earnings_revision_up": ("業績の上方修正", "表題（gen_kaiji.classify の shusei）＋ 向きは表題か fc_revision の dir", "disclosure_time", "docs/tdnet_hist/ ＋ docs/fc_revision/"),
           "earnings_revision_down": ("業績の下方修正", "同上", "disclosure_time", "同上"),
           "dividend_up": ("増配", "表題（classify の haito）＋ 向きは表題か fc_revision の div.dir", "disclosure_time", "同上"),
           "dividend_down": ("減配", "同上", "disclosure_time", "同上"),
           "buyback_decision": ("自己株式の取得の決議", "buyback.json の type=decision ＋ 表題（classify の jisha）", "disclosure_time", "docs/buyback.json ＋ docs/tdnet_hist/"),
           "stock_split": ("株式分割の発表", "表題（classify の bunkatsu）＋ splits.json の比率", "disclosure_time", "docs/tdnet_hist/ ＋ docs/splits.json"),
           "yutai_new": ("株主優待の新設", "表題（classify の yutai・新設）", "disclosure_time", "docs/tdnet_hist/"),
           "yutai_end": ("株主優待の廃止", "表題（classify の yutai・廃止）", "disclosure_time", "docs/tdnet_hist/")})
def detect_disclosure(d, data):
    rows = data.tdnet(d)
    fc = data.fc_revision(d)
    found = {}                                     # (type, code) → {time, ids, titles, params}

    def add(t, row, **params):
        c = code4(row.get("c"))
        g = found.setdefault((t, c), {"time": row.get("t"), "ids": [], "titles": [], "params": {}})
        if row.get("t") and (not g["time"] or row["t"] < g["time"]):
            g["time"] = row["t"]                   # 同じ日に複数あれば早い方の時刻
        if row.get("id") and row["id"] not in g["ids"]:
            g["ids"].append(row["id"])
            g["titles"].append(row.get("ti"))
        for k, v in params.items():
            g["params"].setdefault(k, v)

    for row in rows:
        ti, nm = row.get("ti") or "", row.get("n") or ""
        if not row.get("c") or RE_ETF.search(nfkc_norm(ti)) or RE_ETF.search(nfkc_norm(nm)):
            continue
        for kind, sub in classify(ti):
            if kind == "shusei":
                dr, how = (sub, "title") if sub else _rev_dir(fc, row, "shusei")
                if dr in ("上方", "下方"):
                    add("earnings_revision_up" if dr == "上方" else "earnings_revision_down", row, dir_src=how)
            elif kind == "haito":
                dr, how = (sub, "title") if sub else _rev_dir(fc, row, "haito")
                if dr in ("増配", "減配"):
                    add("dividend_up" if dr == "増配" else "dividend_down", row, dir_src=how)
            elif kind == "jisha":
                add("buyback_decision", row, by="title")
            elif kind == "bunkatsu":
                add("stock_split", row, sub=sub or None)
            elif kind == "yutai" and sub in ("新設", "廃止"):
                add("yutai_new" if sub == "新設" else "yutai_end", row)

    # 自己株式の取得: buyback.json の決議（表題の分類が漏らした分も拾う）
    for it in data.buyback():
        if it.get("type") == "decision" and it.get("date") == d and it.get("code"):
            row = {"c": it["code"], "t": it.get("time"), "id": it.get("id"), "ti": it.get("title")}
            add("buyback_decision", row, by="buyback.json")
            extra = {k: it.get(k) for k in ("amount_max", "shares_max", "pct", "period_from", "period_to", "method", "tostnet")
                     if it.get(k) is not None}
            g = found[("buyback_decision", code4(it["code"]))]
            g.setdefault("snap", {}).update(extra)

    # 株式分割: splits.json の比率・日程
    sp = data.splits()
    for key in ("upcoming", "recent"):
        for x in sp.get(key) or []:
            if x.get("ann") == d and ("stock_split", code4(x.get("code"))) in found:
                g = found[("stock_split", code4(x["code"]))]
                g.setdefault("snap", {}).update({k: x.get(k) for k in ("ratio", "record", "ex", "effective") if x.get(k)})

    out = []
    for (t, c), g in sorted(found.items()):
        snap = {"disclosure_ids": g["ids"], "titles": g["titles"], **(g.get("snap") or {})}
        out.append(ev(t, c, g["params"], snap, time=g["time"], known=time_known(d, g["time"]),
                      src=f"tdnet_hist/{d}.json"))
    return out


# ════════════════════════════════════════ 決算の前（2026-11-19 追加・detector_version 1.0）
# 手元の検証（J-Quants 無料プラン・2024〜2026・私的な検証）で「強い側で確定」した仮説 E1 を、
# 株レーダー自身の記録（JPX の決算発表予定日・TDnet の業績予想の修正）で、今日から当日記録する。
# 過去の統計の数字はどこにも書かない（公開しない）。しきい値・窓は検証の仮説と同じで、後から動かさない。
#
#   窓（検証と同じ）: 発表日の N 営業日前の始値 → 発表日の前営業日の終値（N 本）。N = 10・5
#   その窓の始値で持つには、前の営業日の引け後までに分かっている必要がある。
#   → 出来事の日 d ＝ 予定日の (N+1) 営業日前（d の翌営業日〜予定日の前営業日がちょうど N 営業日）。
#     known = after_close、基準価格 ＝ 翌営業日（＝予定日の N 営業日前）の始値。
#   条件: 前回の決算発表の日 P 〜 d に、通期の営業利益の見通しを +5% 超 上げる修正を出している
#         （修正前の営業利益 > 0 で、修正後 ÷ 修正前 − 1 > 0.05）。
PRE_EARN_NS = (10, 5)
PRE_EARN_UP = 0.05
PRE_EARN_START = "2026-11-19"          # 予定日の「その日の記録」を始めた日。これより前の日は記録しない
FC_REVISION_START = "2026-10-05"       # docs/fc_revision の記録の始まり（最初の修正の開示日）
PRE_EARN_RECENT_DAYS = 35              # 前回の決算からこれより短い予定日は「すでに発表済みで届出が古いまま」とみなして外す
YOTEI_KEEP_DAYS = 45                   # 予定日の記録に残す範囲（d から何暦日先まで）
RE_NOT_OWN = re.compile(r"子会社|親会社")  # 例「連結子会社（株式会社近鉄百貨店）の業績予想の修正」は数えない


def bizdays_after(d, D):
    """d の翌日〜D（D を含む）の営業日の数（あと何営業日）。D <= d なら 0"""
    if D <= d:
        return 0
    n, cur = 0, d
    for _ in range(400):
        cur = next_bizday(cur)
        if cur > D:
            break
        n += 1
    return n


def prev_bizday(d):
    """d より前の最後の営業日（文字列）"""
    from datetime import timedelta
    cur = _date.fromisoformat(d)
    for _ in range(30):
        cur -= timedelta(days=1)
        if is_bizday(cur):
            return cur.isoformat()
    raise ValueError(d)


def yotei_from_kessan(kj, d):
    """docs/kessan.json（その日の版）→ その日の予定日の記録 {"d", "src", "items": {code: 予定日}}。版が違えば None"""
    if not kj or kj.get("today") != d:
        return None
    from datetime import timedelta
    lim = (_date.fromisoformat(d) + timedelta(days=YOTEI_KEEP_DAYS)).isoformat()
    items = {}
    for day in kj.get("days") or []:
        D = day.get("d")
        if not D or D <= d or D > lim:
            continue
        for x in day.get("items") or []:
            c = code4(x.get("c"))
            if c and (c not in items or D < items[c]):
                items[c] = D
    return {"d": d, "src_updated": kj.get("updated"), "jpx_asof": kj.get("asof"),
            "items": dict(sorted(items.items()))}


def kessan_meta(kj):
    """kessan.json → {code: (名前, 四半期)}（表示用）"""
    out = {}
    for day in (kj or {}).get("days") or []:
        for x in day.get("items") or []:
            c = code4(x.get("c"))
            if c:
                out[c] = (unicodedata.normalize("NFKC", x.get("n") or "").strip(),
                          unicodedata.normalize("NFKC", x.get("q") or "").strip())
    return out


def op_revisions(code, since, d, data):
    """since〜d（両端を含む）に出た、通期の業績見通しの修正 → (上げた[+5%超], 向きが分からない, それ以外)"""
    up, unknown, other = [], [], []
    for it in data.fc_revision_all().values():
        if code4(it.get("c")) != code or not it.get("d") or not (since <= it["d"] <= d):
            continue
        if it.get("k") not in (None, "rvfc"):            # 配当（rvdf）・訂正の類（rr*）は見ない
            continue
        if it.get("per") not in ("通期", None):
            continue                                       # 中間期だけの修正は通期の見通しではない
        if RE_NOT_OWN.search(nfkc_norm(it.get("ti"))):
            continue                                       # 子会社・親会社の見通しの修正（その会社自身の見通しではない）
        prev, cur = (it.get("prev") or {}).get("op"), (it.get("cur") or {}).get("op")
        rec = {"d": it["d"], "t": it.get("t"), "id": it.get("id"), "op_prev": prev, "op_cur": cur}
        if it.get("per") is None or prev is None or cur is None:
            unknown.append(rec)                            # 期間・営業利益が読めない → 向きが分からない
            continue
        if prev > 0 and cur > prev * (1 + PRE_EARN_UP) + 1e-9:     # ちょうど +5% は「超」ではない（小数の誤差で入れない）
            rec["pct"] = round((cur / prev - 1) * 100, 1)
            up.append(rec)
        else:
            other.append(rec)
    return sorted(up, key=lambda r: (r["d"], r.get("t") or "")), unknown, other


def pre_earnings_state(d, data, max_left=None):
    """d の引け後の時点で、予定日が d の後 max_left 営業日以内の会社ごとの判定。
    → (rows, asof)。rows = [{code, yotei, left, status, prev_kessan, up, unknown}]
    status: hit（+5%超 上げていた）/ no_raise / prev_before_records（前回の決算が修正の記録の始まりより前）/
            prev_unknown（前回の決算の日が記録に無い）/ unknown_dir（向きが分からない修正だけ）/
            recent_kessan（前回の決算が近すぎる＝すでに発表済みの可能性）"""
    asof = data.yotei_asof(d)
    if not asof:
        return [], None
    max_left = max_left or (max(PRE_EARN_NS) + 1)
    kd = data.kessan_dates()
    rows = []
    for c, D in (asof.get("items") or {}).items():
        left = bizdays_after(d, D)
        if left < 1 or left > max_left:
            continue
        past = [x for x in kd.get(c, []) if x[0] <= d]
        P = past[-1][0] if past else None
        row = {"code": c, "yotei": D, "left": left, "prev_kessan": P, "up": [], "unknown": []}
        if P is None:
            row["status"] = "prev_unknown"
        elif (_date.fromisoformat(D) - _date.fromisoformat(P)).days < PRE_EARN_RECENT_DAYS:
            row["status"] = "recent_kessan"
        elif P < FC_REVISION_START:
            row["status"] = "prev_before_records"
        else:
            up, unk, _ = op_revisions(c, P, d, data)
            row["up"], row["unknown"] = up, unk
            row["status"] = "hit" if up else ("unknown_dir" if unk else "no_raise")
        rows.append(row)
    return sorted(rows, key=lambda r: (r["left"], r["code"])), asof


@detector({f"pre_earnings_up_{n}": (f"決算発表の{n}営業日前・見通しを上げていた",
                                    f"事前に公表された決算発表予定日の{n + 1}営業日前（翌営業日の始値から予定日の前営業日まで"
                                    f"ちょうど{n}営業日）で、前回の決算発表の日〜その日に、通期の営業利益の見通しを"
                                    f"+{int(PRE_EARN_UP * 100)}%超 上げる修正を出している（修正前 > 0）。"
                                    f"前回の決算が {FC_REVISION_START} より前・日が分からない会社、営業利益の見通しが無い会社は対象外",
                                    "after_close", "data/ledger2/kessan_yotei_asof（JPX の決算発表予定日のその日の記録）"
                                    " ＋ docs/fc_revision ＋ docs/kessan_flash_hist・tdnet_hist")
           for n in PRE_EARN_NS})
def detect_pre_earnings(d, data):
    if d < PRE_EARN_START:
        return []
    rows, asof = pre_earnings_state(d, data)
    out = []
    for r in rows:
        if r["status"] != "hit":
            continue
        for n in PRE_EARN_NS:
            if r["left"] != n + 1:
                continue
            best = max(r["up"], key=lambda x: x["pct"])
            out.append(ev(f"pre_earnings_up_{n}", r["code"],
                          {"n": n, "threshold": PRE_EARN_UP, "yotei": r["yotei"], "days_left": r["left"],
                           "prev_kessan": r["prev_kessan"], "rev_pct_max": best["pct"],
                           "rev_last": r["up"][-1]["d"], "n_rev": len(r["up"])},
                          {"yotei": r["yotei"], "yotei_asof": d, "jpx_asof": asof.get("jpx_asof"),
                           "yotei_src_updated": asof.get("src_updated"), "revisions": r["up"]},
                          known="after_close", src=f"kessan_yotei_asof/{d[:7]}.jsonl#{d}"))
    return out


# ════════════════════════════════════════ サイトの判定
def _stage_of_lit(lit):
    for lo, rank, key in GAUGE_STAGES:
        if lit >= lo:
            return rank, key
    return 0, "calm"


@detector({"thermo_stage_change": ("温度計の段階が変わった", "当時公開した温度計の判定（4段階）が前の公開日と違う（radar_published）",
                                   "after_close", "docs/radar_history*.json ＋ docs/thermo100*.json")})
def detect_thermo_stage(d, data):
    items = data.radar_items()
    prev = None
    for it in items:
        if it.get("d") == d:
            if prev and it.get("regime") and prev.get("regime") and it["regime"] != prev["regime"]:
                th = data.thermo_hist().get(d) or {}
                return [ev("thermo_stage_change", "MKT", {"from": prev["regime"], "to": it["regime"], "prev_date": prev["d"],
                            # 判定のルールが前の日と違う（例 2026-10-24 の統一）ときの印。見かけの変化の可能性がある
                            "rule_changed": prev.get("rule") != it.get("rule")},
                           {"vix": it.get("vix"), "n225": it.get("n225"), "rule": it.get("rule"),
                            "thermo": th.get("t"), "celsius": th.get("tc")},
                           known="after_close", src="radar_published")]
            return []
        if it.get("regime"):
            prev = it
    return []


@detector({"gauge_stage_up": ("暴落の傾斜計の段階が上がった", "灯った数で決まる段階（3つ=watch・4つ以上=prepare）が前の日より上がった。5つすべて判定できた日だけ",
                              "after_close", "docs/gauge.json の history")})
def detect_gauge(d, data):
    g = data.gauge() or {}
    hist = sorted(g.get("history") or [], key=lambda x: x["d"])
    total = g.get("total") or 5
    for i, h in enumerate(hist):
        if h["d"] != d or i == 0:
            continue
        p = hist[i - 1]
        if h.get("judged") != total or p.get("judged") != total:
            return []
        r1, k1 = _stage_of_lit(h.get("lit") or 0)
        r0, k0 = _stage_of_lit(p.get("lit") or 0)
        if r1 > r0:
            return [ev("gauge_stage_up", "MKT", {"from": k0, "to": k1, "lit": h.get("lit"), "prev_lit": p.get("lit"),
                                                 "prev_date": p["d"]}, {"lit": h.get("lit"), "total": total},
                       known="after_close", src="gauge.json")]
    return []


# シグナル台帳（docs/ledger）の判定を、判定元ごとの出来事として取り込む。
# regime（温度計）と karauri_new（空売りの新規）は上の検出関数と重なるので取り込まない。
SIGNAL_SOURCES = ("ai", "score3", "crash", "free_scanner", "tenbagger", "movers",
                  "treasure_quiet", "treasure_contra", "treasure_card")


@detector({f"signal_{s}": (f"シグナル台帳: {s}", "docs/ledger/*.jsonl に記録された判定（cause・fix_of の行は除く）",
                           "row_time", "docs/ledger/") for s in SIGNAL_SOURCES})
def detect_signal_ledger(d, data):
    grp = {}
    for r in data.ledger_rows(d):
        if r.get("source") not in SIGNAL_SOURCES or r.get("cause") or r.get("fix_of"):
            continue
        grp.setdefault((r["source"], r["code"]), []).append(r)
    out = []
    for (s, c), rows in sorted(grp.items()):
        t = min((r.get("time") for r in rows if r.get("time")), default=None)
        # 条件の値（cond）は元の台帳の行にある。ここは参照（ledger_ids）だけにして行を軽くする
        out.append(ev(f"signal_{s}", c, {"n": len(rows)},
                      {"ledger_ids": [r["id"] for r in rows]}, time=t, known=time_known(d, t),
                      src=f"ledger/{d[:7]}.jsonl"))
    return out


# ──────────────────────────────────────── 発生時点の状況（context）
MCAP_BANDS = ((1e12, "1兆円以上"), (1e11, "1000億〜1兆円"), (1e10, "100億〜1000億円"), (0, "100億円未満"))
VALUE_BANDS = ((10000, "100億円以上"), (1000, "10億〜100億円"), (100, "1億〜10億円"), (0, "1億円未満"))   # 百万円


def dirty_skip(d, e, data):
    """当日記録の検出で、この出来事を飛ばすか（DIRTY_LOOKBACK の種類で、その銘柄の日足が判定の期間に汚れている）"""
    lb = DIRTY_LOOKBACK.get(e.get("event_type"))
    if lb is None or e.get("code") in (None, "MKT"):
        return False
    return e["code"] in data.px_dirty(d, lb)


def context(d, code, data):
    """温度計の段階・℃、時価総額帯、売買代金帯、業種、7章のチャート項目（取れるものだけ・無いものは null）"""
    th = data.thermo_hist().get(d)
    tn = data.thermo_now() or {}
    if tn.get("trade_date") == d:
        th = {"t": tn.get("value"), "tc": (tn.get("celsius") or {}).get("value"), "r": tn.get("regime")}
    ctx = {"thermo_stage": (th or {}).get("r"), "thermo": (th or {}).get("t"), "thermo_c": (th or {}).get("tc"),
           "mcap_band": None, "value_band": None, "sector33": None, "scale": None,
           "chart": {"above_ma25": None, "above_ma75": None, "above_ma200": None, "dev_ma25": None,
                     "from_high52": None, "ret20": None, "vol_ratio20": None, "atr_level": None}}
    if code == "MKT":
        return ctx
    u = data.universe().get(code) or {}
    ctx["sector33"], ctx["scale"] = u.get("sector33"), u.get("scale")
    p = data.prices(d)
    it = ((p or {}).get("items") or {}).get(code)
    if it and not it.get("stale"):
        sh = data.listed_shares().get(code)
        if sh and it.get("p"):
            mc = sh * it["p"]
            ctx["mcap_band"] = next(l for lo, l in MCAP_BANDS if mc >= lo)
        if it.get("v") is not None:
            ctx["value_band"] = next(l for lo, l in VALUE_BANDS if it["v"] >= lo)
        ch = ctx["chart"]
        ch["dev_ma25"] = it.get("g25")
        ch["above_ma25"] = (it["g25"] > 0) if it.get("g25") is not None else None
        ch["from_high52"] = it.get("hi52")
        ch["ret20"] = it.get("c20")
        ch["vol_ratio20"] = it.get("vr")
        # 株価の汚れの守り: prices.json（Yahoo の1年分）の値が汚れた日足から来ている欄は持たない（null）
        for k, lb in CHART_LOOKBACK.items():
            if ch.get(k) is not None and code in data.px_dirty(d, lb):
                ch[k] = None
    return ctx
