-- 2026-10-25 地合いの正本を検証エンジン版にする切り替え（tools/rewrite_market_regime.py）の前に1回だけ流す。
-- Supabase の SQL Editor で実行する（PostgREST からは列を足せないため）。
--
-- regime_scanner … 2026-10-25 まで正本 regime だった旧スキャナー版の地合い（監査用）。
--                  売買の判定には使わない。前計算（precompute/metrics.py）が毎日書く。
-- 列を足すだけ。既存の行は null のまま（道具の --apply が埋める）。何度流しても同じ結果。

alter table public.market_condition
  add column if not exists regime_scanner text
  check (regime_scanner in ('BULLISH','NEUTRAL','BEARISH','PANIC'));

comment on column public.market_condition.regime_scanner is
  '旧スキャナー版の地合い（2026-10-25 まで regime の正本だったもの）。監査用。売買の判定には使わない';

-- 確かめ（1行返れば列がある）
select column_name, data_type
  from information_schema.columns
 where table_schema = 'public' and table_name = 'market_condition' and column_name = 'regime_scanner';

-- ■ 戻すとき（切り替えをやめて列も消す場合だけ。--rollback の後に）
-- alter table public.market_condition drop column if exists regime_scanner;
