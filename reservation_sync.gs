// ============================================================
// Samurai System - 予約メール自動連携スクリプト
// Google Apps Script にコピーして使用してください
// 対象 Gmail: samuraibeauty.yoyaku@gmail.com
// ============================================================

const SUPABASE_URL = 'https://ifiamddyhbbrseglqesg.supabase.co';
// ※ Supabase ダッシュボード → Settings → API → service_role key を貼り付けてください
//    (anon キーではなく service_role キーを使用すること)
const SUPABASE_SERVICE_KEY = PropertiesService.getScriptProperties().getProperty('SUPABASE_SERVICE_KEY') || '';

const PROCESSED_LABEL = 'samurai-processed'; // 処理済みラベル名

// 店舗名キーワード → store_key マッピング
const STORE_MAP = {
  '西新宿本店': 'nishishinjuku',
  '西新宿':     'nishishinjuku',
  '新宿三丁目': 'sanchome',
  '三丁目':     'sanchome',
  '渋谷東':     'shibuya',
  '渋谷':       'shibuya',
};

// ============================================================
// 日付文字列のパース（JST）
// 対応フォーマット:
//   "2026年04月11日 20:15"         ← サロンコネクト
//   "2026年04月13日（月）19:00"    ← サロンボード
//   "2026/04/13 19:00"
// ============================================================
function parseJpDate(str) {
  if (!str) return { date: null, datetime: null };
  str = str.trim();

  // 年月日形式
  let m = str.match(/(\d{4})年(\d{1,2})月(\d{1,2})日[（(（]?[月火水木金土日]?[）)）]?\s*(\d{1,2}):(\d{2})/);
  if (!m) {
    // スラッシュ形式
    m = str.match(/(\d{4})\/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})/);
  }
  if (!m) return { date: null, datetime: null };

  const y  = m[1];
  const mo = String(m[2]).padStart(2, '0');
  const d  = String(m[3]).padStart(2, '0');
  const h  = String(m[4]).padStart(2, '0');
  const mi = m[5];
  return {
    date:     `${y}-${mo}-${d}`,
    datetime: `${y}-${mo}-${d}T${h}:${mi}:00+09:00`,
  };
}

// 店舗名文字列から store_key を取得
function getStoreKey(text) {
  if (!text) return 'unknown';
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
  if (!SUPABASE_SERVICE_KEY) {
    Logger.log('❌ SUPABASE_SERVICE_KEY が設定されていません。セットアップ手順を確認してください。');
    return 0;
  }
  const res = UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/reservations', {
    method: 'POST',
    headers: {
      'apikey':        SUPABASE_SERVICE_KEY,
      'Authorization': 'Bearer ' + SUPABASE_SERVICE_KEY,
      'Content-Type':  'application/json',
      'Prefer':        'resolution=merge-duplicates,return=minimal',
    },
    payload:            JSON.stringify(data),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  if (code >= 400) {
    Logger.log('❌ Supabase エラー [' + code + ']: ' + res.getContentText());
  }
  return code;
}

// ============================================================
// サロンコネクト メール解析
// 送信元: noreply@salonconnect.jp
// ============================================================
function parseSalonConnect(body, subject) {
  const isCancelled = subject.includes('キャンセル') || body.includes('キャンセル');

  const idMatch = body.match(/予約番号[：:]\s*(\d+)/);
  if (!idMatch) {
    Logger.log('  → 予約番号が見つかりません（サロンコネクト）');
    return null;
  }

  const storeMatch = body.match(/(?:予約受付店舗|店舗名)[：:]\s*(.+)/);
  const storeName  = storeMatch ? storeMatch[1].trim() : '';

  const dateMatch = body.match(/(?:来店日時|予約日時|日時)[：:]\s*(.+)/);
  const { date, datetime } = dateMatch ? parseJpDate(dateMatch[1]) : {};

  const nameMatch = body.match(/(?:お名前|氏名)[：:]\s*(.+)/);
  const customerName = nameMatch ? nameMatch[1].trim().replace(/\s*様$/, '').trim() : '';

  const staffMatch = body.match(/(?:指名|担当)[：:]\s*(.+)/);
  const staffRaw   = staffMatch ? staffMatch[1].trim() : '';
  const staffName  = (staffRaw.startsWith('※') || staffRaw === '指名なし') ? '' : staffRaw;

  const menuMatch = body.match(/メニュー[：:]\s*(.+)/);
  const menuRaw   = menuMatch ? menuMatch[1].trim() : '';
  const menu      = menuRaw.startsWith('※') ? '' : menuRaw;

  const durMatch = body.match(/所要時間[：:]\s*(\d+)\s*分/);

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
    amount:           null,
    status:           isCancelled ? 'cancelled' : 'confirmed',
  };
}

// ============================================================
// サロンボード メール解析
// 送信元: yoyaku_system@salonboard.com
// ============================================================
function parseSalonBoard(body, subject) {
  const isCancelled = subject.includes('キャンセル') || body.includes('キャンセル');

  const idMatch = body.match(/■予約番号\s+([A-Z0-9\-]+)/);
  if (!idMatch) {
    Logger.log('  → 予約番号が見つかりません（サロンボード）');
    return null;
  }

  // 店舗名：メール本文中の "SamuraiBeauty 〇〇" または "■店舗名 〇〇" 形式
  let storeName = '';
  const storeMatch1 = body.match(/■店舗名\s+(.+)/);
  const storeMatch2 = body.match(/SamuraiBeauty[\s　]([^\n【]+)/);
  if (storeMatch1)      storeName = storeMatch1[1].trim();
  else if (storeMatch2) storeName = 'SamuraiBeauty ' + storeMatch2[1].trim();

  const dateMatch = body.match(/■(?:来店日時|予約日時|来店日)\s+(.+)/);
  const { date, datetime } = dateMatch ? parseJpDate(dateMatch[1]) : {};

  const nameMatch    = body.match(/■氏名\s+(.+)/);
  const customerName = nameMatch
    ? nameMatch[1].trim().replace(/（[^）]*）/, '').replace(/\s*様$/, '').trim()
    : '';

  const staffMatch = body.match(/■指名スタッフ\s+(.+)/);
  const staffRaw   = staffMatch ? staffMatch[1].trim() : '';
  const staffName  = (staffRaw === '指名なし' || staffRaw === '') ? '' : staffRaw;

  // メニューは ■メニュー の後、次の ■ まで
  const menuMatch = body.match(/■メニュー\s+([\s\S]+?)(?=\n■|\n\n|$)/);
  const menu      = menuMatch ? menuMatch[1].trim().split('\n')[0].trim() : '';

  const amountMatch = body.match(/(?:お支払予定金額|料金)\s+([0-9,]+円)/);

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
    amount:           amountMatch ? amountMatch[1] : null,
    status:           isCancelled ? 'cancelled' : 'confirmed',
  };
}

// ============================================================
// メイン処理（トリガーで定期実行）
// ============================================================
function processReservationEmails() {
  if (!SUPABASE_SERVICE_KEY) {
    Logger.log('❌ SUPABASE_SERVICE_KEY が未設定です。setupServiceKey() を先に実行してください。');
    return;
  }

  const label = getOrCreateLabel(PROCESSED_LABEL);

  const searches = [
    { query: 'from:noreply@salonconnect.jp -label:' + PROCESSED_LABEL,      parser: 'sc' },
    { query: 'from:yoyaku_system@salonboard.com -label:' + PROCESSED_LABEL, parser: 'sb' },
  ];

  let saved = 0, skipped = 0;

  searches.forEach(({ query, parser }) => {
    const threads = GmailApp.search(query, 0, 50);
    Logger.log(`[${parser}] ${threads.length} スレッド検索`);

    threads.forEach(thread => {
      thread.getMessages().forEach(msg => {
        const body    = msg.getPlainBody();
        const subject = msg.getSubject();
        Logger.log(`  メール: "${subject}"`);

        const data = parser === 'sc'
          ? parseSalonConnect(body, subject)
          : parseSalonBoard(body, subject);

        if (data && data.reservation_date) {
          const code = upsertReservation(data);
          if (code < 300) {
            Logger.log(`  ✅ 保存: ${data.reservation_id} ${data.customer_name} ${data.datetime}`);
            saved++;
          } else {
            skipped++;
          }
        } else {
          Logger.log('  ⚠ パース失敗: ' + subject);
          skipped++;
        }
      });
      thread.addLabel(label);
    });
  });

  Logger.log(`===== 完了: 保存 ${saved}件 / スキップ ${skipped}件 =====`);
}

// ============================================================
// デバッグ用: メール本文を確認（最新5通ずつ）
// ============================================================
function debugEmails() {
  ['from:noreply@salonconnect.jp', 'from:yoyaku_system@salonboard.com'].forEach(q => {
    const threads = GmailApp.search(q, 0, 5);
    threads.forEach(thread => {
      const msg = thread.getMessages()[0];
      Logger.log('=== ' + msg.getSubject() + ' ===');
      Logger.log(msg.getPlainBody().slice(0, 800));
      Logger.log('---');
    });
  });
}

// ============================================================
// セットアップ手順:
//
// Step 1: Supabase のサービスロールキーを設定
//   → setupServiceKey() 関数を実行（下を参照）
//
// Step 2: Supabase ダッシュボードで以下のSQLを実行
//   → supabase_setup.sql の内容を実行
//
// Step 3: このスクリプトを samuraibeauty.yoyaku@gmail.com の
//   Google アカウントで開き、権限を許可
//
// Step 4: processReservationEmails を一度手動実行してテスト
//   → ログを確認（保存件数、エラー内容）
//
// Step 5: トリガーを設定
//   「トリガーを追加」→ processReservationEmails
//   → 時間ベース → 10分ごと
// ============================================================

// Supabase service_role キーを Script Properties に保存
// ※ 実行前に下の YOUR_SERVICE_ROLE_KEY を実際のキーに変更すること
function setupServiceKey() {
  const key = 'YOUR_SERVICE_ROLE_KEY'; // ← ここに貼り付け
  PropertiesService.getScriptProperties().setProperty('SUPABASE_SERVICE_KEY', key);
  Logger.log('✅ SUPABASE_SERVICE_KEY を保存しました');
}
