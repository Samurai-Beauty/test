// ==UserScript==
// @name         サムライ → Salon Connect シフト自動同期
// @namespace    https://samurai-beauty.github.io/test/
// @version      2.0.2
// @description  サムライのシフトをSalon Connectへ自動入力。各スタッフの店舗別アカウントに、その日の実働店舗で振り分け（多店舗アカウント・店舗別パターンID対応）
// @author       Samurai Beauty
// @match        https://sc.salonconnect.jp/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // ── 設定 ────────────────────────────────────────────────────────────────
  var SB_URL   = 'https://ifiamddyhbbrseglqesg.supabase.co';
  var SB_KEY   = 'sb_publishable_nUMDcYGE4ZzkBQAiV0bvCQ_9t1bthno';
  var PLAN_KEY = 'samurai_sc_sync_v1';    // localStorage キー（SCドメイン内）
  var TASK_STORE_KEY = 'sc_sync_task';    // Supabase store_key
  var POLL_MS  = 4000;   // タスク確認間隔（ms）
  var SAVE_WAIT_MS = 2500; // 保存後の待機時間

  // 事前テスト用：true にすると「保存せず」フォームに入力結果を表示するだけ（プレビュー）。
  // 各スタッフごとに一時停止し、カレンダーを目視確認 → [次へ] で進む。本番では false に戻す。
  var DRY_RUN = false;

  // サムライ名 → 店舗別 Salon Connect アカウントID（2026年7月確認済み）
  // ※ 各スタッフは「勤務する店舗ごとに別アカウント」を持つ。
  //    その日の実働店舗（店舗振替を考慮）に対応するアカウントへ書き込む＝混在しない。
  //    shopId: 西新宿=7701, 三丁目=7487, 渋谷=7699
  var STAFF_ACCOUNTS = {
    '矢澤南奈': { '7701':'47849', '7487':'48968' },
    '清瀬陽香': { '7701':'48970', '7487':'47923' },
    '高橋里奈': { '7701':'47851', '7487':'46693' },
    '沖中真奈': { '7701':'48219', '7487':'47922' },
    '三浦さら': {                 '7487':'49731' }, // 三丁目のみ
    // 退職: 岡部実結・小川真央 は登録しない
  };
  // スタッフの自店（店舗振替が無い日のデフォルト勤務店）
  var STAFF_HOME = {
    '矢澤南奈':'7701','清瀬陽香':'7701','高橋里奈':'7701',
    '沖中真奈':'7487','三浦さら':'7487',
  };
  var SHOP_LABEL = { '7701':'西新宿', '7487':'三丁目', '7699':'渋谷' };

  // 全アカウント（スタッフ×店舗）を1リストに展開
  function buildAccounts() {
    var list = [];
    Object.keys(STAFF_ACCOUNTS).forEach(function(name) {
      var accs = STAFF_ACCOUNTS[name];
      Object.keys(accs).forEach(function(store) {
        list.push({ name: name, store: store, scId: accs[store] });
      });
    });
    return list;
  }

  // サムライ シフトキー → Salon Connect シフトパターンID（店舗別／2026年7月確認済み）
  // ※ 店舗ごとにパターンIDが異なるため shopId で引く（混在防止の最重要ポイント）
  var SHOP_SHIFT_MAP = {
    // 西新宿（7701）
    '7701': {
      'h11-22':'14775','h11-17':'14776','early':'14777',
      'full':'14778','late':'14780','omakase':'14778','off':null,
    },
    // 三丁目（7487）
    '7487': {
      'h11-22':'14781','h11-17':'14782','early':'14784',
      'full':'14783','late':'14786','omakase':'14783','off':null,
    },
  };
  // カスタム時間文字列 → 店舗別パターンID（SCに完全一致する枠が無い場合は最寄りに丸める）
  var SHOP_CUSTOM_MAP = {
    '7701': {
      '11:00-22:00':'14775','11:00-18:00':'14776','11:00-17:00':'14776',
      '13:00-18:00':'14777','13:00-22:00':'14778','15:00-22:00':'14779',
      '16:00-22:00':'14780','17:00-22:00':'14780','18:00-22:00':'14780',
      '11:00-16:00':'15107','11:00-15:00':'15107','11:00-16:45':'15107',
    },
    '7487': {
      '11:00-22:00':'14781','11:00-18:00':'14782','11:00-17:00':'14782',
      '13:00-18:00':'14784','13:00-22:00':'14783','15:00-22:00':'14785',
      '16:00-22:00':'14786','17:00-22:00':'14786','18:00-22:00':'14786',
      // 三丁目に11:00-16時台の枠が無いため最寄りの11:00-17:00(14782)へ丸める
      '11:00-16:00':'14782','11:00-15:00':'14782','11:00-16:45':'14782',
    },
  };
  // サムライ店舗キー → shopId（店舗振替 ssched_stores 用）
  var STORE_SHOP = { 'nishishinjuku':'7701','sanchome':'7487','shibuya':'7699' };
  // ────────────────────────────────────────────────────────────────────────

  // ── Supabase ヘルパー ─────────────────────────────────────────────────
  function sbGet(storeKey) {
    return fetch(
      SB_URL + '/rest/v1/sales_data?store_key=eq.' + encodeURIComponent(storeKey) + '&select=data_json',
      { headers: { 'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY } }
    ).then(function(r){ return r.ok ? r.json() : []; }).catch(function(){ return []; });
  }

  function sbDelete(storeKey) {
    return fetch(
      SB_URL + '/rest/v1/sales_data?store_key=eq.' + encodeURIComponent(storeKey),
      {
        method: 'DELETE',
        headers: {
          'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY, 'Prefer': 'return=minimal'
        }
      }
    ).catch(function(){});
  }

  // ── ローカルプラン（ページをまたいで状態を保持） ─────────────────────
  function getPlan() {
    try { return JSON.parse(localStorage.getItem(PLAN_KEY) || 'null'); }
    catch(e) { return null; }
  }

  function setPlan(p) {
    if (p) localStorage.setItem(PLAN_KEY, JSON.stringify(p));
    else   localStorage.removeItem(PLAN_KEY);
  }

  // ── 進捗UIパネル ─────────────────────────────────────────────────────
  var _uiEl = null;
  var _stopped = false;

  function showUI(msg, done, total) {
    if (!_uiEl) {
      _uiEl = document.createElement('div');
      _uiEl.style.cssText = [
        'position:fixed;top:16px;right:16px;z-index:2147483647',
        'background:#0f3460;color:#fff;padding:16px 18px',
        'border-radius:14px;box-shadow:0 6px 28px rgba(0,0,0,.4)',
        'font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif',
        'font-size:13px;max-width:280px;line-height:1.55',
        'min-width:220px',
      ].join(';');
      document.body.appendChild(_uiEl);
    }
    var pct = total > 0 ? Math.round(done / total * 100) : 0;
    _uiEl.innerHTML = [
      '<div style="font-weight:800;font-size:14px;margin-bottom:8px">📅 サムライ SC同期</div>',
      '<div style="margin-bottom:10px">' + escHtml(msg) + '</div>',
      total > 0 ? [
        '<div style="background:rgba(255,255,255,.18);border-radius:4px;height:6px;overflow:hidden">',
        '  <div style="background:#4fc3f7;height:6px;width:' + pct + '%;transition:width .4s"></div>',
        '</div>',
        '<div style="font-size:11px;margin-top:5px;opacity:.75">' + done + ' / ' + total + '人完了 (' + pct + '%)</div>',
      ].join('') : '',
      '<button id="sc-sync-stop" style="margin-top:10px;padding:5px 14px;background:rgba(255,255,255,.15);',
      'color:#fff;border:1px solid rgba(255,255,255,.3);border-radius:7px;cursor:pointer;font-size:12px">',
      '■ 停止</button>',
    ].join('');
    document.getElementById('sc-sync-stop').onclick = function() {
      _stopped = true;
      setPlan(null);
      showUI('⛔ 同期を停止しました', 0, 0);
      setTimeout(hideUI, 3000);
    };
  }

  function hideUI() {
    if (_uiEl) { _uiEl.remove(); _uiEl = null; }
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── 事前テスト（プレビュー）用 ─────────────────────────────────────────
  function logPlan(name, shopId, dayPlan) {
    var rows = dayPlan.filter(function(p){ return p.status !== 'skip'; }).map(function(p){
      return { 日付: p.dateStr, 状態: p.status === 'work' ? '出勤' : '休み', パターンID: p.pid || '' };
    });
    console.log('%c[SC同期] ' + name + '（店舗' + shopId + '）', 'font-weight:bold;color:#0f3460');
    if (console.table) console.table(rows); else console.log(rows);
  }

  function showPreview(name, workN, remaining, done, total, onNext) {
    if (!_uiEl) showUI('', done, total);
    _uiEl.innerHTML = [
      '<div style="font-weight:800;font-size:14px;margin-bottom:8px">👀 プレビュー（保存しません）</div>',
      '<div style="margin-bottom:8px"><b>' + escHtml(name) + '</b>：出勤 ' + workN + '日を入力しました。<br>カレンダーを目視確認してください。</div>',
      '<div style="font-size:11px;opacity:.75;margin-bottom:10px">進捗 ' + done + ' / ' + total + '名（残り' + remaining + '名）</div>',
      '<button id="sc-prev-next" style="padding:6px 16px;background:#4fc3f7;color:#06243a;border:none;border-radius:7px;cursor:pointer;font-size:13px;font-weight:800">次のスタッフへ ▶</button>',
      '<button id="sc-prev-stop" style="margin-left:8px;padding:6px 14px;background:rgba(255,255,255,.15);color:#fff;border:1px solid rgba(255,255,255,.3);border-radius:7px;cursor:pointer;font-size:12px">■ 終了</button>',
    ].join('');
    document.getElementById('sc-prev-next').onclick = function(){ onNext(); };
    document.getElementById('sc-prev-stop').onclick = function(){
      _stopped = true; setPlan(null); showUI('⛔ プレビューを終了しました', 0, 0); setTimeout(hideUI, 3000);
    };
  }

  // ── シフト計算 ─────────────────────────────────────────────────────────
  function resolvePid(key, shopId) {
    if (!key) return null;
    if (key === 'off') return null;
    var keyMap    = SHOP_SHIFT_MAP[shopId]  || SHOP_SHIFT_MAP['7701'];
    var customMap = SHOP_CUSTOM_MAP[shopId] || SHOP_CUSTOM_MAP['7701'];
    if (key.startsWith('custom:')) {
      var t = key.slice(7).replace(/~/g, '-'); // 「18:00~22:00」等のチルダを正規化
      return customMap[t] || null;             // 不明なカスタムは null（誤ったIDを入れない）
    }
    var v = keyMap[key];
    return v !== undefined ? v : null;
  }

  // store = このアカウントの店舗。その日の実働店舗が store と一致する日だけ「出勤」、
  // それ以外は必ず「休み」。全日を明示的に上書きするのでゴミ（出1 等）が残らない。
  function calcPlan(staffName, year, mon, shiftData, store) {
    var ssched = shiftData.ssched        || {};
    var stimes = shiftData.ssched_times  || {};
    var sstore = shiftData.ssched_stores || {};
    var home   = STAFF_HOME[staffName] || store;
    var days   = new Date(year, mon, 0).getDate();
    var mp     = String(mon).padStart(2, '0');
    var result = [];
    for (var d = 1; d <= days; d++) {
      var ds     = String(d);
      var dateStr= year + '-' + mp + '-' + String(d).padStart(2, '0');
      var inSch  = (ssched[ds] || []).indexOf(staffName) >= 0;
      var tkey   = (stimes[ds] || {})[staffName];
      var pid    = null;

      if (inSch && tkey !== 'off') {
        // その日の実働店舗（振替があればその店、無ければ自店）
        var ovStore = (sstore[ds] || {})[staffName];
        var effShop = ovStore ? (STORE_SHOP[ovStore] || home) : home;
        if (effShop === store) {
          pid = resolvePid(tkey || 'full', store);
        }
      }
      result.push({ dateStr: dateStr, status: pid ? 'work' : 'off', pid: pid });
    }
    return result;
  }

  // ── フォーム入力 ────────────────────────────────────────────────────────
  function setVal(el, val) {
    if (!el) return;
    try {
      var p = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      var s = Object.getOwnPropertyDescriptor(p, 'value');
      if (s && s.set) s.set.call(el, val); else el.value = val;
    } catch(e) { el.value = val; }
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function fillForm(dayPlan) {
    var n = 0;
    dayPlan.forEach(function(p) {
      if (p.status === 'skip') return;
      var ce = document.querySelector('[name="closed_'           + p.dateStr + '"]');
      var se = document.querySelector('[name="shift_id_'         + p.dateStr + '"]');
      var be = document.querySelector('[name="before_shiftid_'   + p.dateStr + '"]');
      if (!ce && !se) return;
      if (p.status === 'work' && p.pid) {
        setVal(ce, '0'); setVal(se, p.pid); if (be) setVal(be, p.pid);
      } else {
        // off、または有効なパターンIDが取れなかった work → 休みとして確定（誤ID防止）
        setVal(ce, '1'); setVal(se, ''); if (be) setVal(be, '');
      }
      n++;
    });
    return n;
  }

  function clickSave() {
    // セレクター順に保存ボタンを探す
    var sels = [
      'input[type="submit"][value*="保存"]',
      'button[type="submit"]',
      'input[type="submit"]',
      'button[onclick*="submit"]',
    ];
    for (var i = 0; i < sels.length; i++) {
      var el = document.querySelector(sels[i]);
      if (el) { el.click(); return true; }
    }
    // テキストで "保存" を含むボタンを探す
    var allBtns = document.querySelectorAll('button, input[type="button"]');
    for (var j = 0; j < allBtns.length; j++) {
      if (allBtns[j].textContent.includes('保存') || (allBtns[j].value || '').includes('保存')) {
        allBtns[j].click();
        return true;
      }
    }
    // 最終手段: フォームを直接送信
    var form = document.getElementById('jsubmit_form') || document.querySelector('form[method="post"]');
    if (form) { form.submit(); return true; }
    return false;
  }

  // ── メイン同期ループ（アカウント単位＝スタッフ×店舗） ────────────────
  function gotoAccount(scId, ym) {
    location.href =
      '/client_admin/staff_day_shift.php' +
      '?select_staff=' + encodeURIComponent(scId) +
      '&yearmonth=' + encodeURIComponent(ym);
  }

  function runNext() {
    if (_stopped) return;
    var plan = getPlan();
    if (!plan || !plan.accounts) return;

    var remaining = plan.accounts.filter(function(a) {
      return plan.done.indexOf(a.scId) < 0;
    });
    var total = plan.accounts.length;
    var done  = total - remaining.length;

    if (remaining.length === 0) {
      if (DRY_RUN) {
        showUI('👀 プレビュー完了（保存なし）。問題なければ DRY_RUN を false にして再実行してください。', total, total);
        setPlan(null);
        return;
      }
      showUI('✅ 全' + total + 'アカウントの同期が完了しました！', total, total);
      setPlan(null);
      sbDelete(TASK_STORE_KEY);
      setTimeout(hideUI, 6000);
      return;
    }

    var next  = remaining[0];
    var name  = next.name;
    var store = next.store;
    var scId  = next.scId;
    var label = name + '（' + (SHOP_LABEL[store] || store) + '）';
    var ym    = plan.ym;  // '202607'

    showUI(label + ' を処理中…（残り' + remaining.length + '件）', done, total);

    var params = new URLSearchParams(location.search);
    var curStaff = params.get('select_staff');
    var curYm    = params.get('yearmonth');
    var onShiftPage = location.pathname.indexOf('staff_day_shift') >= 0;

    if (onShiftPage && curStaff === scId && curYm === ym) {
      // 正しいアカウントのページにいる → 入力
      var year = parseInt(ym.slice(0, 4), 10);
      var mon  = parseInt(ym.slice(4, 6), 10);
      var dayPlan = calcPlan(name, year, mon, plan.shiftData, store);
      var filled  = fillForm(dayPlan);
      logPlan(label, store, dayPlan);

      // ── 事前テスト（プレビュー）：保存せず目視確認 ──
      if (DRY_RUN) {
        var workN = dayPlan.filter(function(p){ return p.status === 'work'; }).length;
        showPreview(label, workN, remaining.length, done, total, function() {
          plan.done.push(scId);
          setPlan(plan);
          goNext();
        });
        return;
      }

      showUI(label + ': ' + filled + '日設定、保存中…', done, total);

      setTimeout(function() {
        if (_stopped) return;
        var saved = clickSave();
        if (!saved) {
          showUI('⚠️ ' + label + ': 保存ボタンが見つかりません。手動で保存してください。', done, total);
          setTimeout(function() {
            plan.done.push(scId);
            setPlan(plan);
            runNext();
          }, 5000);
          return;
        }
        plan.done.push(scId);
        setPlan(plan);

        setTimeout(function() {
          if (_stopped) return;
          // AJAX保存でページが変わらなければ次のアカウントへ
          if (location.pathname.indexOf('staff_day_shift') >= 0 &&
              new URLSearchParams(location.search).get('select_staff') === scId) {
            goNext();
          }
          // ページリロードが起きた場合は onPageReady → runNext() で続行
        }, SAVE_WAIT_MS);
      }, 600);

    } else {
      goNext();
    }

    function goNext() {
      if (_stopped) return;
      var plan2 = getPlan();
      if (!plan2 || !plan2.accounts) return;
      var rem2 = plan2.accounts.filter(function(a){ return plan2.done.indexOf(a.scId) < 0; });
      if (rem2.length === 0) { runNext(); return; }
      gotoAccount(rem2[0].scId, plan2.ym);
    }
  }

  // ── 起動処理 ─────────────────────────────────────────────────────────
  function onPageReady() {
    var plan = getPlan();

    if (plan && plan.accounts) {
      // ローカルプランあり → 同期を続ける
      showUI('同期を再開しています…', 0, plan.accounts.length);
      setTimeout(runNext, 1200);
      return;
    }

    // ローカルプランなし → Supabase でタスクをポーリング
    var pollTimer = setInterval(function() {
      sbGet(TASK_STORE_KEY).then(function(rows) {
        if (!rows || rows.length === 0) return;
        var task = rows[0].data_json;
        if (!task || task.status !== 'pending') return;

        clearInterval(pollTimer);

        // タスクをローカルプランに変換（アカウント割り当ては userscript 側で解決）
        var accounts = buildAccounts();
        var newPlan = {
          ym:        task.ym,
          shiftData: task.shiftData,
          accounts:  accounts,
          done:      [],
        };
        setPlan(newPlan);

        // 確認ダイアログ
        var year = task.ym.slice(0, 4);
        var mon  = parseInt(task.ym.slice(4, 6), 10);
        var names = Object.keys(STAFF_ACCOUNTS).join('、');

        if (!confirm(
          (DRY_RUN ? '【サムライ SC同期：プレビュー（保存しません）】\n\n'
                   : '【サムライ SC自動同期】\n\n') +
          year + '年' + mon + '月のシフトを全アカウントに' + (DRY_RUN ? '入力（保存なし）' : '自動入力・保存') + 'します。\n\n' +
          '対象(' + Object.keys(STAFF_ACCOUNTS).length + '名 / ' + accounts.length + 'アカウント): ' + names + '\n\n' +
          '※各スタッフの「その日の実働店舗」に対応するアカウントへ振り分けます。\n\n' +
          (DRY_RUN ? 'アカウントごとに一時停止し、目視確認しながら進めます。よろしいですか？'
                   : '自動でフォームを入力・保存します。よろしいですか？')
        )) {
          setPlan(null);
          if (!DRY_RUN) sbDelete(TASK_STORE_KEY);
          return;
        }

        runNext();
      });
    }, POLL_MS);
  }

  // DOM準備完了後に起動
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onPageReady);
  } else {
    setTimeout(onPageReady, 800);
  }
})();
