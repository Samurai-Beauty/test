// ============================================================
// Samurai System - 予約メール自動連携スクリプト
// Google Apps Script にコピーして使用してください
// ============================================================

const SUPABASE_URL = 'https://ifiamddyhbbrseglqesg.supabase.co';
const SUPABASE_KEY = 'sb_publishable_nUMDcYGE4ZzkBQAiV0bvCQ_9t1bthno';
const PROCESSED_LABEL = 'samurai-processed'; // 処理済みラベル名

// 店舗名キーワード → store_key マッピング
const STORE_MAP = {
  '西新宿本店': 'nishishinjuku',
  '新宿三丁目': 'sanchome',
  '渋谷東':     'shibuya',
};

// ============================================================
// 日付文字列のパース（JST）
// 対応フォーマット:
//   "2026年04月11日 20:15"  ← サロンコネクト
//   "2026年04月13日（月）19:00"  ← サロンボード
// ============================================================
function parseJpDate(str) {
  const m = str.match(/(\d{4})年(\d{2})月(\d{2})日[（(]?[月火水木金土日]?[）)]?\s*(\d{2}):(\d{2})/);
  if (!m) return { date: null, datetime: null };
  return {
    date:     `${m[1]}-${m[2]}-${m[3]}`,
    datetime: `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00+09:00`,
  };
}

// 店舗名文字列から store_key を取得
function getStoreKey(text) {
  for (const [name, key] of Object.entries(STORE_MAP)) {
    if (text.includes(name)) return key;
  }
  return 'unknown';
}

// Gmailラベルを取得（なければ作成）
function getOrCreateLabel(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

// Supabase に予約データをアップサート（reservation_id で重複防止）
function upsertReservation(data) {
  const res = UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/reservations', {
    method: 'POST',
    headers: {
      'apikey':        SUPABASE_KEY,
      'Authorization': 'Bearer ' + SUPABASE_KEY,
      'Content-Type':  'application/json',
      'Prefer':        'resolution=merge-duplicates',
    },
    payload:           JSON.stringify(data),
    muteHttpExceptions: true,
  });
  return res.getResponseCode();
}

// ============================================================
// サロンコネクト メール解析
// 送信元: noreply@salonconnect.jp
// ============================================================
function parseSalonConnect(body, subject) {
  const isCancelled = subject.includes('キャンセル');

  const storeMatch = body.match(/(?:予約受付店舗|店舗名)：(.+)/);
  const storeName  = storeMatch ? storeMatch[1].trim() : '';

  const idMatch = body.match(/予約番号：(\d+)/);
  if (!idMatch) return null;

  const dateMatch = body.match(/日時：(.+)/);
  const { date, datetime } = dateMatch ? parseJpDate(dateMatch[1].trim()) : {};

  const nameMatch = body.match(/お名前：(.+)/);
  const customerName = nameMatch ? nameMatch[1].trim() : '';

  const durMatch  = body.match(/所要時間：(\d+)分/);

  const staffMatch = body.match(/指名：(.+)/);
  const staffRaw   = staffMatch ? staffMatch[1].trim() : '';
  const staffName  = staffRaw.startsWith('※') ? '' : staffRaw;

  const menuMatch = body.match(/メニュー：(.+)/);
  const menuRaw   = menuMatch ? menuMatch[1].trim() : '';
  const menu      = menuRaw.startsWith('※') ? '' : menuRaw;

  return {
    reservation_id:   'SC-' + idMatch[1],
    source:           'salonconnect',
    store_key:        getStoreKey(storeName),
    store_name:       storeName,
    reservation_date: date     || null,
    datetime:         datetime || null,
    customer_name:    customerName,
    staff_name:       staffName,
    menu:             menu,
    duration_min:     durMatch ? parseInt(durMatch[1]) : null,
    amount:           '',
    status:           isCancelled ? 'cancelled' : 'confirmed',
  };
}

// ============================================================
// サロンボード メール解析
// 送信元: yoyaku_system@salonboard.com
// ============================================================
function parseSalonBoard(body, subject) {
  const storeMatch = body.match(/SamuraiBeauty ([^【\n]+)/);
  const storeName  = storeMatch ? ('SamuraiBeauty ' + storeMatch[1].trim()) : '';

  const idMatch = body.match(/■予約番号\s+([A-Z0-9]+)/);
  if (!idMatch) return null;

  const nameMatch    = body.match(/■氏名\s+(.+)/);
  const customerName = nameMatch
    ? nameMatch[1].trim().replace(/（[^）]+）/, '').trim()
    : '';

  const dateMatch = body.match(/■来店日時\s+(.+)/);
  const { date, datetime } = dateMatch ? parseJpDate(dateMatch[1].trim()) : {};

  const staffMatch = body.match(/■指名スタッフ\s+(.+)/);
  const staffRaw   = staffMatch ? staffMatch[1].trim() : '';
  const staffName  = staffRaw === '指名なし' ? '' : staffRaw;

  // メニューは次の■が来るまでの最初の行
  const menuMatch = body.match(/■メニュー\s+([\s\S]+?)(?=■)/);
  const menu      = menuMatch ? menuMatch[1].trim().split('\n')[0].trim() : '';

  const amountMatch = body.match(/お支払予定金額\s+([0-9,]+円)/);

  return {
    reservation_id:   'SB-' + idMatch[1],
    source:           'salonboard',
    store_key:        getStoreKey(storeName),
    store_name:       storeName,
    reservation_date: date     || null,
    datetime:         datetime || null,
    customer_name:    customerName,
    staff_name:       staffName,
    menu:             menu,
    duration_min:     null,
    amount:           amountMatch ? amountMatch[1] : '',
    status:           'confirmed',
  };
}

// ============================================================
// メイン処理（トリガーで定期実行）
// ============================================================
function processReservationEmails() {
  const label = getOrCreateLabel(PROCESSED_LABEL);

  const searches = [
    { query: 'from:noreply@salonconnect.jp -label:' + PROCESSED_LABEL,       parser: 'sc' },
    { query: 'from:yoyaku_system@salonboard.com -label:' + PROCESSED_LABEL,  parser: 'sb' },
  ];

  let processed = 0;

  searches.forEach(({ query, parser }) => {
    const threads = GmailApp.search(query, 0, 50);
    threads.forEach(thread => {
      thread.getMessages().forEach(msg => {
        const body    = msg.getPlainBody();
        const subject = msg.getSubject();

        const data = parser === 'sc'
          ? parseSalonConnect(body, subject)
          : parseSalonBoard(body, subject);

        if (data && data.datetime) {
          const code = upsertReservation(data);
          Logger.log(`[${data.source}] ${data.reservation_id} → HTTP ${code} / ${data.customer_name} / ${data.datetime}`);
          processed++;
        } else {
          Logger.log('スキップ: ' + msg.getSubject());
        }
      });
      thread.addLabel(label); // 処理済みラベルを付与
    });
  });

  Logger.log('===== 処理完了: ' + processed + '件 =====');
}

// ============================================================
// セットアップ手順:
//
// 1. script.google.com を開く
// 2. 「新しいプロジェクト」→ このコードを貼り付けて保存
// 3. 「processReservationEmails」を選択して▶実行
//    （初回はGmailアクセス権限の許可が必要）
// 4. 動作確認後、トリガーを設定:
//    「トリガーを追加」→ 関数: processReservationEmails
//    → 時間ベース → 10分ごと
// ============================================================
