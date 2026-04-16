-- ============================================================
-- Samurai System - Supabase 予約テーブル セットアップSQL
-- Supabase ダッシュボード → SQL Editor で実行してください
-- ============================================================

-- 1. reservations テーブルの作成（既に存在する場合はスキップ）
CREATE TABLE IF NOT EXISTS reservations (
  id               BIGSERIAL PRIMARY KEY,
  reservation_id   TEXT UNIQUE,          -- SC-12345 / SB-ABC123 （重複防止キー）
  source           TEXT,                 -- salonconnect / salonboard
  store_key        TEXT,                 -- nishishinjuku / sanchome / shibuya
  store_name       TEXT,
  reservation_date DATE,                 -- 予約日 (YYYY-MM-DD)
  datetime         TIMESTAMPTZ,          -- 予約日時 (JST)
  customer_name    TEXT,
  staff_name       TEXT,
  menu             TEXT,
  duration_min     INTEGER,
  amount           TEXT,
  status           TEXT DEFAULT 'confirmed',  -- confirmed / cancelled
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- 2. 既存テーブルに reservation_id カラムが無い場合に追加
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS reservation_id TEXT;

-- 3. reservation_id にユニーク制約を追加（upsert に必要）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'reservations_reservation_id_key'
  ) THEN
    ALTER TABLE reservations ADD CONSTRAINT reservations_reservation_id_key UNIQUE (reservation_id);
  END IF;
END $$;

-- 4. RLS を有効化
ALTER TABLE reservations ENABLE ROW LEVEL SECURITY;

-- 5. SELECT ポリシー（フロントエンド＝anon キーで読み取り可能にする）
DROP POLICY IF EXISTS "allow_anon_select" ON reservations;
CREATE POLICY "allow_anon_select" ON reservations
  FOR SELECT TO anon
  USING (true);

-- 6. INSERT/UPDATE ポリシー（GAS から service_role キーで書き込む場合は不要だが念のため）
DROP POLICY IF EXISTS "allow_anon_insert" ON reservations;
CREATE POLICY "allow_anon_insert" ON reservations
  FOR INSERT TO anon
  WITH CHECK (true);

DROP POLICY IF EXISTS "allow_anon_update" ON reservations;
CREATE POLICY "allow_anon_update" ON reservations
  FOR UPDATE TO anon
  USING (true) WITH CHECK (true);

-- 7. インデックス（日付検索の高速化）
CREATE INDEX IF NOT EXISTS idx_reservations_date ON reservations (reservation_date);
CREATE INDEX IF NOT EXISTS idx_reservations_store ON reservations (store_key, reservation_date);

-- 確認クエリ（実行後にテーブル内容を確認）
-- SELECT * FROM reservations ORDER BY created_at DESC LIMIT 20;
