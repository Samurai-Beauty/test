// ============================================================
// Samurai System - エルメ カルテ回答 同期スクリプト
// Google Apps Script にコピーして使用してください
//
// 【初期設定】スクリプトプロパティに以下を登録：
//   ANTHROPIC_API_KEY  : Anthropic Console で発行したAPIキー
//   ELME_OAUTH_TOKEN   : エルメMCP の OAuthアクセストークン
//   SUPABASE_SERVICE_KEY : Supabase の service_role キー
//   ERROR_EMAIL        : エラー通知先メールアドレス（例: 辰巳大地のGmail）
//
// 【トリガー設定】
//   realtimeSync → 毎分（時間ベース → 1分ごと）※カルテリアルタイム閲覧用
//   weeklySync   → 毎週月曜 09:00
//   monthlySync  → 毎月1日  09:00
// ============================================================

const SUPABASE_URL      = 'https://ifiamddyhbbrseglqesg.supabase.co';
const ELME_BOT_ID       = 'aRo9dx';
const ELME_FORM_ID      = '201419'; // ご新規カルテ
const ELME_MCP_URL      = 'https://mcp.lmes.jp/mcp';
const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages';
const ANTHROPIC_MODEL   = 'claude-haiku-4-5-20251001';

// エルメ フォーム「来店店舗」→ Supabase store_key
const ELME_STORE_MAP = {
  '西新宿小滝橋通り 店': 'nishishinjuku',
  '新宿三丁目 店':       'sanchome',
  '渋谷東 店':           'shibuya',
};

// ============================================================
// トリガーエントリポイント
// ============================================================

// ── リアルタイム閲覧用（毎分トリガー） ──────────────────────
// 直近2時間のカルテ回答を個別データとして karte_live キーに保存
// Supabase store_key: 'karte_live'
function realtimeSync() {
  const props        = PropertiesService.getScriptProperties();
  const anthropicKey = props.getProperty('ANTHROPIC_API_KEY');
  const elmeToken    = props.getProperty('ELME_OAUTH_TOKEN');
  const sbKey        = props.getProperty('SUPABASE_SERVICE_KEY');

  const to   = new Date();
  const from = new Date(to.getTime() - 2 * 60 * 60 * 1000); // 直近2時間

  try {
    const responses = fetchKarteRaw(anthropicKey, elmeToken, from, to);
    if (!responses || responses.length === 0) return;

    // 既存データと合算（response_id で重複除去、最新200件）
    const existing   = fetchSupabaseData('karte_live', sbKey) || [];
    const existingIds = new Set(existing.map(function(r) { return r.response_id; }));
    const newOnes    = responses.filter(function(r) { return !existingIds.has(r.response_id); });
    if (newOnes.length === 0) return;

    const merged = newOnes.concat(existing).slice(0, 200);

    UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/sales_data', {
      method:  'post',
      headers: {
        'Content-Type':  'application/json',
        'apikey':        sbKey,
        'Authorization': 'Bearer ' + sbKey,
        'Prefer':        'resolution=merge-duplicates',
      },
      payload: JSON.stringify({
        store_key:   'karte_live',
        data_json:   merged,
        uploaded_at: new Date().toISOString(),
        uploaded_by: 'gas_karte_realtime',
      }),
      muteHttpExceptions: true,
    });

    Logger.log('karte_live 更新: +' + newOnes.length + '件（合計' + merged.length + '件）');
  } catch(e) {
    Logger.log('realtimeSync エラー: ' + e.message);
  }
}

// 個別フォーム回答を全フィールド付きで取得
function fetchKarteRaw(anthropicKey, elmeToken, from, to) {
  const prompt = [
    'エルメのご新規カルテフォームの回答を取得してください。',
    'bot_id: ' + ELME_BOT_ID,
    'form_id: ' + ELME_FORM_ID,
    '対象期間: ' + fmtDate(from) + ' から ' + fmtDate(to) + ' まで（answered_at で絞り込む）',
    '',
    '全件取得後、以下のJSON配列のみを返してください。余分な説明は不要です：',
    '[',
    '  {',
    '    "response_id": <number>,',
    '    "answered_at": "YYYY-MM-DDTHH:mm:ss",',
    '    "store": "<来店店舗のvalue>",',
    '    "inflow": "<流入経路のvalue>",',
    '    "name": "<氏名（あれば）>",',
    '    "phone": "<電話番号（あれば）>",',
    '    "menu": "<希望メニュー（あれば）>"',
    '  },',
    '  ...',
    ']',
    '',
    '対象期間外の回答は除外すること。フィールドがない場合は null とすること。',
  ].join('\n');

  const payload = {
    model:      ANTHROPIC_MODEL,
    max_tokens: 8192,
    mcp_servers: [{
      type:                'url',
      url:                 ELME_MCP_URL,
      name:                'elme',
      authorization_token: elmeToken,
    }],
    messages: [{ role: 'user', content: prompt }],
  };

  const res = UrlFetchApp.fetch(ANTHROPIC_API_URL, {
    method:  'post',
    headers: {
      'Content-Type':      'application/json',
      'x-api-key':         anthropicKey,
      'anthropic-version': '2023-06-01',
      'anthropic-beta':    'mcp-client-2025-04-04',
    },
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  if (res.getResponseCode() !== 200) {
    throw new Error('Anthropic API エラー ' + res.getResponseCode());
  }

  const body  = JSON.parse(res.getContentText());
  const text  = body.content.filter(function(b) { return b.type === 'text'; }).map(function(b) { return b.text; }).join('');
  const match = text.match(/\[[\s\S]*\]/);
  if (!match) throw new Error('JSONが返答に含まれていません: ' + text.slice(0, 200));
  return JSON.parse(match[0]);
}

// 毎週月曜 09:00 に実行（直近7日分）
function weeklySync() {
  const to   = new Date();
  const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
  Logger.log('週次同期開始: ' + fmtDate(from) + ' 〜 ' + fmtDate(to));
  syncKarteResponses(from, to, 'weekly');
}

// 毎月1日 09:00 に実行（前月1日〜末日）
function monthlySync() {
  const now   = new Date();
  const from  = new Date(now.getFullYear(), now.getMonth() - 1, 1, 0, 0, 0);
  const to    = new Date(now.getFullYear(), now.getMonth(),     0, 23, 59, 59);
  Logger.log('月次同期開始: ' + fmtDate(from) + ' 〜 ' + fmtDate(to));
  syncKarteResponses(from, to, 'monthly');
}

// ============================================================
// メイン同期処理
// ============================================================
function syncKarteResponses(from, to, mode) {
  const props = PropertiesService.getScriptProperties();
  const anthropicKey = props.getProperty('ANTHROPIC_API_KEY');
  const elmeToken    = props.getProperty('ELME_OAUTH_TOKEN');
  const sbKey        = props.getProperty('SUPABASE_SERVICE_KEY');
  const errorEmail   = props.getProperty('ERROR_EMAIL');

  try {
    // 1. AnthropicAPI + エルメMCP でフォーム回答を取得
    const responses = fetchKarteViaAnthropicMCP(anthropicKey, elmeToken, from, to);
    if (!responses || responses.length === 0) {
      Logger.log('対象期間の回答なし');
      return;
    }
    Logger.log('取得件数: ' + responses.length + '件');

    // 2. 店舗×月 ごとに集計
    const monthly = aggregateByMonthStore(responses);

    // 3. Supabase に upsert（inflow_${store}_${y}-${m} キー）
    saveToSupabase(monthly, sbKey);

    Logger.log('同期完了 (' + mode + ')');

  } catch (e) {
    Logger.log('エラー: ' + e.message);
    if (errorEmail) {
      GmailApp.sendEmail(
        errorEmail,
        '[Samurai System] エルメ同期エラー',
        '同期モード: ' + mode + '\n\nエラー内容:\n' + e.message + '\n\n' + e.stack
      );
    }
    throw e;
  }
}

// ============================================================
// Anthropic API + エルメMCP でフォーム回答を取得
// ============================================================
function fetchKarteViaAnthropicMCP(anthropicKey, elmeToken, from, to) {
  const fromStr = fmtDate(from);
  const toStr   = fmtDate(to);

  const prompt = [
    'エルメのご新規カルテフォームの回答を取得してください。',
    'bot_id: ' + ELME_BOT_ID,
    'form_id: ' + ELME_FORM_ID,
    '対象期間: ' + fromStr + ' から ' + toStr + ' まで（answered_at で絞り込む）',
    '',
    '全件取得後、以下のJSON配列のみを返してください。余分な説明は不要：',
    '[',
    '  {',
    '    "response_id": <number>,',
    '    "answered_at": "YYYY-MM-DD",',
    '    "store": "<来店店舗のvalue>",',
    '    "inflow": "<Samurai Beautyをどのようにお知りになりましたか？のvalue>"',
    '  },',
    '  ...',
    ']',
    '',
    '注意: 対象期間外の回答は除外すること。個人情報（氏名・電話・住所）は含めないこと。',
  ].join('\n');

  const payload = {
    model:      ANTHROPIC_MODEL,
    max_tokens: 8192,
    mcp_servers: [{
      type:                'url',
      url:                 ELME_MCP_URL,
      name:                'elme',
      authorization_token: elmeToken,
    }],
    messages: [{ role: 'user', content: prompt }],
  };

  const res = UrlFetchApp.fetch(ANTHROPIC_API_URL, {
    method:  'post',
    headers: {
      'Content-Type':      'application/json',
      'x-api-key':         anthropicKey,
      'anthropic-version': '2023-06-01',
      'anthropic-beta':    'mcp-client-2025-04-04',
    },
    payload:              JSON.stringify(payload),
    muteHttpExceptions:   true,
  });

  const status = res.getResponseCode();
  const body   = JSON.parse(res.getContentText());

  if (status !== 200) {
    throw new Error('Anthropic API エラー ' + status + ': ' + JSON.stringify(body));
  }

  // Claudeの返答からJSONを抽出
  const text = body.content.filter(b => b.type === 'text').map(b => b.text).join('');
  const match = text.match(/\[[\s\S]*\]/);
  if (!match) {
    Logger.log('Claude応答: ' + text);
    throw new Error('フォーム回答のJSONが返答に含まれていません');
  }

  return JSON.parse(match[0]);
}

// ============================================================
// 店舗 × 年月 で新規客数を集計
// ============================================================
function aggregateByMonthStore(responses) {
  // { "2026-5": { nishishinjuku: 10, sanchome: 5, shibuya: 3 } }
  const result = {};

  responses.forEach(function(r) {
    const storeKey = ELME_STORE_MAP[r.store];
    if (!storeKey) return; // 未知の店舗はスキップ

    const d   = new Date(r.answered_at);
    const key = d.getFullYear() + '-' + (d.getMonth() + 1); // "2026-5"

    if (!result[key]) result[key] = { nishishinjuku: 0, sanchome: 0, shibuya: 0 };
    result[key][storeKey]++;
  });

  return result;
}

// ============================================================
// Supabase sales_data テーブルに upsert
// キー形式: inflow_${storeKey}_${y}-${m}  （既存Samurai Systemと同じ）
// ============================================================
function saveToSupabase(monthly, sbKey) {
  const stores = Object.keys(ELME_STORE_MAP).map(function(k) { return ELME_STORE_MAP[k]; });
  const saved  = [];

  Object.keys(monthly).forEach(function(ym) {
    const parts = ym.split('-');
    const y = parseInt(parts[0]);
    const m = parseInt(parts[1]);
    const counts = monthly[ym];

    stores.forEach(function(storeKey) {
      const newClientCount = counts[storeKey] || 0;
      const supabaseKey    = 'inflow_' + storeKey + '_' + y + '-' + m;

      // 既存データを取得してマージ（手入力のサブスク数を上書きしない）
      const existing = fetchSupabaseData(supabaseKey, sbKey) || {};
      const merged = Object.assign({}, existing, { new_client: newClientCount });

      const res = UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/sales_data', {
        method:  'post',
        headers: {
          'Content-Type':  'application/json',
          'apikey':        sbKey,
          'Authorization': 'Bearer ' + sbKey,
          'Prefer':        'resolution=merge-duplicates',
        },
        payload: JSON.stringify({
          store_key:   supabaseKey,
          filename:    'inflow_' + storeKey + '_' + y + '-' + m + '.json',
          data_json:   merged,
          uploaded_at: new Date().toISOString(),
          uploaded_by: 'gas_karte_sync',
        }),
        muteHttpExceptions: true,
      });

      if (res.getResponseCode() >= 300) {
        throw new Error('Supabase保存失敗 [' + supabaseKey + ']: ' + res.getContentText());
      }
      saved.push(supabaseKey + ' = ' + newClientCount + '件');
    });
  });

  Logger.log('Supabase保存完了:\n' + saved.join('\n'));
}

// ============================================================
// Supabase から既存 data_json を取得
// ============================================================
function fetchSupabaseData(storeKey, sbKey) {
  const res = UrlFetchApp.fetch(
    SUPABASE_URL + '/rest/v1/sales_data?store_key=eq.' + encodeURIComponent(storeKey) + '&select=data_json',
    {
      headers: {
        'apikey':        sbKey,
        'Authorization': 'Bearer ' + sbKey,
      },
      muteHttpExceptions: true,
    }
  );
  const rows = JSON.parse(res.getContentText());
  return (rows && rows.length > 0) ? rows[0].data_json : null;
}

// ============================================================
// ユーティリティ
// ============================================================
function fmtDate(d) {
  return d.getFullYear() + '-'
    + String(d.getMonth() + 1).padStart(2, '0') + '-'
    + String(d.getDate()).padStart(2, '0');
}

// ============================================================
// 手動テスト用（GASエディタから直接実行）
// 直近30日分を取得してログ確認
// ============================================================
function testSync() {
  const to   = new Date();
  const from = new Date(to.getTime() - 30 * 24 * 60 * 60 * 1000);
  Logger.log('テスト実行: ' + fmtDate(from) + ' 〜 ' + fmtDate(to));
  syncKarteResponses(from, to, 'test');
}
