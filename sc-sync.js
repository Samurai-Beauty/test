/**
 * サムライシステム → Salon Connect シフト自動入力スクリプト
 * ブックマークレット経由でSalon Connectのスタッフシフトページで実行する
 *
 * 使い方:
 *   1. Salon Connectのスタッフシフトページを開く
 *      例: /staff_day_shift.php?select_staff=47848&yearmonth=202606
 *   2. ブックマークレット「サムライ→SC同期」をクリック
 *   3. プレビューを確認してOKを押す
 *   4. ページ下部の「保存する」ボタンを押す
 */
(function () {
  'use strict';

  // ── 設定 ────────────────────────────────────────────────────────────────
  var SB_URL = 'https://ifiamddyhbbrseglqesg.supabase.co';
  var SB_KEY = 'sb_publishable_nUMDcYGE4ZzkBQAiV0bvCQ_9t1bthno';

  // Salon Connect スタッフID → サムライシステム表示名
  var STAFF_MAP = {
    '46693': '高橋里奈',  // 西新宿
    '47922': '沖中真奈',  // 三丁目
    '47923': '清瀬陽香',  // 西新宿
    '48968': '矢澤南奈',  // 西新宿
    '49731': '三浦さら',  // 三丁目
  };

  // サムライ シフトキー → Salon Connect シフトパターンID（2026年6月確認済み）
  var SHIFT_ID_MAP = {
    'h11-22':  '14781',  // 11:00-22:00 → 11~22
    'h11-17':  '14782',  // 11:00-17:00 → 11~18（近似値）
    'early':   '14784',  // 13:00-18:00 → 13~18
    'late':    '14786',  // 16:00-22:00 → 17~22（近似値）
    'full':    '14783',  // 13:00-22:00 → 13~22
    'omakase': '1',      // お任せ → 全日
    'off':     null,     // 休日希望（休み扱い）
  };

  // カスタム時間（例: 'custom:11:00-16:00'）の変換テーブル
  var CUSTOM_MAP = {
    '11:00-22:00': '14781',
    '11:00-18:00': '14782',
    '11:00-17:00': '14782',
    '13:00-18:00': '14784',
    '13:00-22:00': '14783',
    '15:00-22:00': '14785',
    '16:00-22:00': '14786',
    '17:00-22:00': '14786',
  };
  // ────────────────────────────────────────────────────────────────────────

  var params = new URLSearchParams(location.search);
  var staffId = params.get('select_staff');
  var ymStr   = params.get('yearmonth');

  if (!staffId || !ymStr || ymStr.length !== 6) {
    alert(
      'このスクリプトはSalon Connectのスタッフシフトページで実行してください。\n' +
      '例: /staff_day_shift.php?select_staff=47848&yearmonth=202606'
    );
    return;
  }

  var staffName = STAFF_MAP[staffId];
  if (!staffName) {
    alert(
      'スタッフID ' + staffId + ' がマッピングに見つかりません。\n' +
      'sc-sync.js 内の STAFF_MAP にIDと名前を追加してください。\n\n' +
      '現在登録済みID: ' + Object.keys(STAFF_MAP).join(', ')
    );
    return;
  }

  var year     = ymStr.slice(0, 4);
  var monthPad = ymStr.slice(4, 6);           // '06'
  var monthInt = parseInt(monthPad, 10);       // 6
  var monthKey = parseInt(year, 10) + '-' + monthPad; // 'shift_2026-06' 形式
  var supabaseKey = 'shift_' + monthKey;

  // ── Supabase からシフトデータ取得 ─────────────────────────────────────
  fetch(
    SB_URL + '/rest/v1/sales_data?store_key=eq.' + encodeURIComponent(supabaseKey) + '&select=data_json',
    { headers: { 'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY } }
  )
  .then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  })
  .then(function (rows) {
    if (!rows || rows.length === 0) {
      alert(
        year + '年' + monthInt + '月のシフトデータがSupabaseに見つかりません。\n' +
        'サムライシステムで先にシフトを作成・保存してください。\n' +
        '（検索キー: ' + supabaseKey + '）'
      );
      return;
    }

    var d      = rows[0].data_json || {};
    var ssched = d.ssched        || {};
    var stimes = d.ssched_times  || {};
    var sreq   = d.sreq          || {};
    var isPublished = d.spub === '1';

    var daysInMonth = new Date(parseInt(year, 10), monthInt, 0).getDate();

    // ── 入力プランを作成 ──────────────────────────────────────────────
    var plan = [];
    for (var day = 1; day <= daysInMonth; day++) {
      var dayStr  = String(day);
      var dateStr = year + '-' + monthPad + '-' + (day < 10 ? '0' + day : day);

      var inSched = (ssched[dayStr] || []).indexOf(staffName) >= 0;
      var timeKey = (stimes[dayStr] || {})[staffName];
      var reqKey  = (sreq[staffName] || {})[dayStr];

      var status, patternId;

      if (inSched) {
        if (timeKey === 'off') {
          status = 'off';
        } else if (timeKey) {
          patternId = resolvePatternId(timeKey);
          status    = patternId !== null ? 'work' : 'off';
        } else {
          // スケジュールにあるが時間未設定 → 全日
          status    = 'work';
          patternId = '1';
        }
      } else if (reqKey === 'off' || (isPublished && !inSched)) {
        // 公開済みシフトでssched未収録 → 休み
        status = 'off';
      } else {
        // 未公開かつssched未収録 → スキップ
        status = 'skip';
      }

      plan.push({ day: day, dateStr: dateStr, status: status, patternId: patternId, timeKey: timeKey });
    }

    // ── 確認ダイアログ ────────────────────────────────────────────────
    var workDays = plan.filter(function (p) { return p.status === 'work'; });
    var offDays  = plan.filter(function (p) { return p.status === 'off';  });
    var skipDays = plan.filter(function (p) { return p.status === 'skip'; });

    var lines = [
      '【' + staffName + '】 ' + year + '年' + monthInt + '月 シフト入力プレビュー',
      '公開状態: ' + (isPublished ? '✅ 公開済み' : '⚠️ 未公開（スキップ日多め）'),
      '',
      '出勤: ' + workDays.length + '日  休み: ' + offDays.length + '日  スキップ: ' + skipDays.length + '日',
      '',
    ];

    // 出勤日一覧（最大10件）
    if (workDays.length) {
      lines.push('▶ 出勤日:');
      workDays.slice(0, 10).forEach(function (p) {
        lines.push('  ' + monthInt + '/' + p.day + ' ' + labelOf(p.timeKey));
      });
      if (workDays.length > 10) lines.push('  …他' + (workDays.length - 10) + '日');
      lines.push('');
    }

    if (skipDays.length) {
      lines.push('⚠️ スキップ（入力しない）: ' + skipDays.length + '日');
      lines.push('   ※ シフト未公開のため未確定日はスキップします');
      lines.push('');
    }

    lines.push('OKを押すとフォームに入力します。');
    lines.push('保存は入力後に「保存する」ボタンを押してください。');

    if (!confirm(lines.join('\n'))) return;

    // ── フォームへ入力 ────────────────────────────────────────────────
    var filled = 0;
    plan.forEach(function (p) {
      if (p.status === 'skip') return;

      var closedEl = document.querySelector('[name="closed_'     + p.dateStr + '"]');
      var shiftEl  = document.querySelector('[name="shift_id_'   + p.dateStr + '"]');
      var beforeEl = document.querySelector('[name="before_shiftid_' + p.dateStr + '"]');

      if (!closedEl && !shiftEl) return;

      if (p.status === 'off') {
        setVal(closedEl, '1');
        setVal(shiftEl,  '');
        if (beforeEl) setVal(beforeEl, '');
      } else {
        setVal(closedEl, '0');
        setVal(shiftEl,  p.patternId || '1');
        if (beforeEl) setVal(beforeEl, p.patternId || '1');
      }
      filled++;
    });

    alert(
      '✅ ' + filled + '日分を入力しました。\n\n' +
      'ページ下部の「保存する」ボタンを押して保存してください。'
    );
  })
  .catch(function (err) {
    alert('エラーが発生しました:\n' + err.message);
  });

  // ── ヘルパー ──────────────────────────────────────────────────────────
  function resolvePatternId(key) {
    if (!key) return null;
    if (key.startsWith('custom:')) {
      var t = key.slice(7);
      return CUSTOM_MAP[t] || '1';
    }
    if (key === 'off') return null;
    return SHIFT_ID_MAP[key] !== undefined ? SHIFT_ID_MAP[key] : '1';
  }

  function labelOf(key) {
    if (!key) return '全日';
    if (key.startsWith('custom:')) return key.slice(7).replace(/:00/g, '');
    var m = { 'h11-22':'11-22', 'h11-17':'11-17', 'early':'13-18', 'late':'16-22', 'full':'13-22', 'omakase':'任', 'off':'休' };
    return m[key] || key;
  }

  function setVal(el, val) {
    if (!el) return;
    try {
      var proto  = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      var setter = Object.getOwnPropertyDescriptor(proto, 'value');
      if (setter && setter.set) setter.set.call(el, val);
      else el.value = val;
    } catch (e) { el.value = val; }
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
})();
