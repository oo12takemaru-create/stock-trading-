# -*- coding: utf-8 -*-
"""東京証券取引所の営業日（土日・祝日・年末年始を除いた日）

■ なぜ要るか
  ToSTNeT-3 の買付けは「引け後に開示 → 翌営業日の寄付前」に執行される。
  この「翌営業日」を土日だけで数えると、祝日明けの日付を1日ずらして間違える。
  実例: 2026-08-10(月)開示 → 8/11 は山の日で立会なし → 正解は 8/12(水)。
  （TDnetの開示件数でも 2026-08-11 だけ0件で、休場だったことが裏取りできる）

■ 祝日は法律どおり計算する（表を毎年書き換えなくて済むように）
  ハッピーマンデー・振替休日・国民の休日（例: 敬老の日と秋分の日に挟まれた日）を含む。
  春分／秋分は近似式で求める。1980〜2099年でのみ正しいので、
  範囲外は例外にして黙って間違えないようにする。

■ 取りこぼす可能性があるもの（分かったうえで受け入れている）
  - 五輪のような一度きりの祝日移動（2020年など。過去のぶんなので今の収集には無関係）
  - 大発会・大納会の時間短縮（休場ではないので営業日判定には影響しない）
  - 臨時休場（システム障害など。予測できない）
"""
from datetime import date, timedelta
from functools import lru_cache

YEAR_MIN, YEAR_MAX = 1980, 2099   # 春分・秋分の近似式が使える範囲


def _nth_monday(y, m, n):
    """その月の第n月曜"""
    first = date(y, m, 1)
    return first + timedelta(days=(0 - first.weekday()) % 7 + 7 * (n - 1))


def _equinox(y, spring):
    """春分の日・秋分の日（1980〜2099年で有効な近似式）"""
    base = 20.8431 if spring else 23.2488
    return int(base + 0.242194 * (y - 1980) - (y - 1980) // 4)


@lru_cache(maxsize=None)
def holidays(y):
    """その年の休場日（祝日＋振替休日＋国民の休日＋年末年始）"""
    if not (YEAR_MIN <= y <= YEAR_MAX):
        raise ValueError(f"{y}年は祝日を計算できる範囲（{YEAR_MIN}〜{YEAR_MAX}）の外")
    base = {
        date(y, 1, 1),                    # 元日
        _nth_monday(y, 1, 2),             # 成人の日
        date(y, 2, 11),                   # 建国記念の日
        date(y, 2, 23),                   # 天皇誕生日
        date(y, 3, _equinox(y, True)),    # 春分の日
        date(y, 4, 29),                   # 昭和の日
        date(y, 5, 3),                    # 憲法記念日
        date(y, 5, 4),                    # みどりの日
        date(y, 5, 5),                    # こどもの日
        _nth_monday(y, 7, 3),             # 海の日
        date(y, 8, 11),                   # 山の日
        _nth_monday(y, 9, 3),             # 敬老の日
        date(y, 9, _equinox(y, False)),   # 秋分の日
        _nth_monday(y, 10, 2),            # スポーツの日
        date(y, 11, 3),                   # 文化の日
        date(y, 11, 23),                  # 勤労感謝の日
    }
    # 振替休日: 日曜と重なったら、祝日でない次の日を休みにする
    for d in sorted(base):
        if d.weekday() == 6:
            n = d + timedelta(days=1)
            while n in base:
                n += timedelta(days=1)
            base.add(n)
    # 国民の休日: 祝日に挟まれた平日（敬老の日と秋分の日の間など）
    for d in sorted(base):
        mid = d + timedelta(days=1)
        if mid not in base and mid.weekday() != 6 and (mid + timedelta(days=1)) in base:
            base.add(mid)
    # 取引所の年末年始休業（元日は上で入っている）
    base |= {date(y, 1, 2), date(y, 1, 3), date(y, 12, 31)}
    return frozenset(base)


def is_bizday(d):
    """立会のある日か（土日でも祝日でもない）"""
    return d.weekday() < 5 and d not in holidays(d.year)


def next_bizday(d):
    """翌営業日。d が文字列(YYYY-MM-DD)なら文字列で返す"""
    as_str = isinstance(d, str)
    cur = date.fromisoformat(d) if as_str else d
    for _ in range(30):                    # 年末年始でも10日を超えることはない
        cur += timedelta(days=1)
        if is_bizday(cur):
            return cur.isoformat() if as_str else cur
    raise ValueError(f"{d} の翌営業日が30日以内に見つからない")
