# -*- coding: utf-8 -*-
"""米国株（NYSE・Nasdaq）の休場日と、米国東部時間の扱い（米国株ヒートマップ・2026-10-24）

■ 休場日は表で持つ（NYSE の公表。年1回の手入れ）
  cron-worker/src/us_market.js にも同じ表がある（Worker は Python を読めないため）。
  年を足すときは両方を直す。KNOWN_YEARS には「実際に埋めた年」だけを書く（holidays.js と同じ約束）。
■ 半日立会（13:00 ET 引け）は休場ではないので表に入れない（EARLY_CLOSE に参考として持つ）。
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# NYSE Holidays（https://www.nyse.com/markets/hours-calendars）
US_HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027（6/19 が土曜→6/18、7/4 が日曜→7/5、12/25 が土曜→12/24）
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}
KNOWN_YEARS = (2026, 2027)


def is_us_session(d):
    """その日（米国東部の日付）に立会があるか"""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


def now_et(now=None):
    return (now or datetime.now(ET)).astimezone(ET)


def session_closed(trade_date, now=None):
    """trade_date（YYYY-MM-DD・米国東部）の立会が引けているか。
    当日なら 16:00 ET（半日立会は 13:00）を過ぎたら引け。前日以前なら引け。"""
    n = now_et(now)
    today = n.date().isoformat()
    if trade_date < today:
        return True
    if trade_date > today:
        return False
    close_h = 13 if trade_date in EARLY_CLOSE else 16
    return n.hour >= close_h


def prev_session(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    d -= timedelta(days=1)
    while not is_us_session(d):
        d -= timedelta(days=1)
    return d
