# -*- coding: utf-8 -*-
"""
merge_signals_log.py ― signals_log.csv の「行を絶対に失わない」和集合マージ

GitHub Actions のコミット競合対策(2026-07-13)。
リモート最新の signals_log.csv に、自分のスキャンで追記した行のうち
リモートに無いものだけを追記する(リモートの行は一切消さない)。

使い方:
  python merge_signals_log.py <自分の版(退避コピー)> <マージ先(リモート最新)>
"""
import sys


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python merge_signals_log.py <ours.csv> <target.csv>")
    ours_path, target_path = sys.argv[1], sys.argv[2]

    with open(target_path, encoding="utf-8-sig") as f:
        remote = f.read().splitlines()
    with open(ours_path, encoding="utf-8-sig") as f:
        ours = f.read().splitlines()

    seen = set(remote)
    added = [l for l in ours if l.strip() and l not in seen]
    merged = remote + added

    # 先頭にBOM(utf-8-sig)を維持: ダッシュボード/load_seen_csvは utf-8-sig で読む
    with open(target_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(merged) + "\n")

    print(f"[merge] リモート{len(remote)}行 + 自分の新規{len(added)}行 = {len(merged)}行")


if __name__ == "__main__":
    main()
