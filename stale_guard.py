# -*- coding: utf-8 -*-
"""古い相場の値で新しい値を上書きしない（巻き戻り防止）

■ 何が起きていたか
  crash.json は深夜0〜3時ごろの回が yfinance から古い足を受け取り、
  夕方に正しく入った値を前日・前々日のものに巻き戻していた（直近80版で15回）。
  updated は新しくなるので、鮮度を見る監視には引っかからない。
  radar.json も、スキャナーが走らなかった日に古い signal.json を読んで
  2週間前の地合いを publish していた（同 3回）。

■ 考え方は gauge の last_good と同じ
  「取れなかった」「古いものしか取れなかった」ときに、既に出ている正しい値を
  壊さない。書かずに見送れば、前の値がそのまま残る。

■ 見送ったことは黙らない
  書かなければ updated が進まないので、その日のうちに鮮度チェック（許容0営業日）が
  拾う。静かに古いものを出し続けるより、止まったと分かるほうがよい。
"""
import json
from pathlib import Path


def keep_newest(dst, data, key, label="データ", **dump):
    """既存ファイルの `key` より新しい（または同じ）ときだけ書く。

    戻り値: 書いたら True、見送ったら False。
    比較は文字列のまま行う。日付も ISO のタイムスタンプも、桁が揃っていれば
    辞書順＝時刻順になる。
    """
    dst = Path(dst)
    new = data.get(key)
    if not new:
        # 比べる材料が無いときは素通しする（判定できないことを理由に止めない）
        _write(dst, data, dump)
        return True
    old = None
    try:
        old = json.loads(dst.read_text(encoding="utf-8")).get(key)
    except Exception:
        pass
    if old and str(new) < str(old):
        print(f"⚠️ {label}: 今回取れたのは {new}、すでに出ているのは {old}。"
              f"古い値で上書きしないため書き込みを見送ります")
        return False
    _write(dst, data, dump)
    return True


def _write(dst, data, dump=None):
    """dump には json.dumps の引数をそのまま渡す（既存ファイルの体裁を変えないため）"""
    opts = {"ensure_ascii": False, "separators": (",", ":")}
    opts.update(dump or {})
    if "indent" in opts:
        opts.pop("separators", None)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, **opts), encoding="utf-8")
