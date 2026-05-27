// ==UserScript==
// @name         サムライ → Salon Connect シフト自動同期
// @namespace    https://samurai-beauty.github.io/test/
// @version      1.1.0
// @description  サムライシステムで公開したシフトをSalon Connectに全スタッフ自動入力する
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

  // Salon Connect スタッフID → { name: サムライ名, shopId: 店舗ID }
  // ※ shopId: 西新宿=7701, 三丁目=7487, 渋谷=7699
  var STAFF_MAP = {
    '46693': { name: '高橋里奈',   shopId: '7701' }, // Rina   - 西新宿
    '47923': { name: '清瀬陽香',   shopId: '7701' }, // Haruka - 西新宿
    '48817': { name: '小川真央',   shopId: '7701' }, // Mao    - 西新宿
    '48968': { name: '矢澤南奈',   shopId: '7701' }, // Nana   - 西新宿
    '48455': { name: '岡部実結',   shopId: '7487' }, // Miyuu  - 三丁目
    '49731': { name: '三浦さら',   shopId: '7487' }, // Sara   - 三丁目
    // '46694': { name: '沖中真奈 or 中山菜々江?', shopId: '7487' }, // Yuri  ← 要確認
    // '47922': { name: '???',  shopId: '7699' }, // Sakura ← 要確認
  };

  // サムライ シフトキー → Salon Connect シフトパターンID（2026年6月確認済み）
  var SHIFT_ID_MAP = {
    'h11-22': '14781', 'h11-17': '14782',
    'early':  '14784', 'late':   '14786',
    'full':   '14783', 'omakase':'1',
    'off':    null,
  };
  var CUSTOM_MAP = {
    '11:00-22:00':'14781','11:00-18:00':'14782','11:00-17:00':'14782',
    '13:00-18:00':'14784','13:00-22:00':'14783',
    '15:00-22:00':'14785','16:00-22:00':'14786','17:00-22:00':'14786',
  };
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

  // ── シフト計算 ─────────────────────────────────────────────────────────
  function resolvePid(key) {
    if (!key) return null;
    if (key === 'off') return null;
    if (key.startsWith('custom:')) return CUSTOM_MAP[key.slice(7)] || '1';
    var v = SHIFT_ID_MAP[key];
    return v !== undefined ? v : '1';
  }

  function calcPlan(staffName, year, mon, shiftData) {
    var ssched = shiftData.ssched       || {};
    var stimes = shiftData.ssched_times || {};
    var sreq   = shiftData.sreq         || {};
    var pub    = shiftData.spub === '1';
    var days   = new Date(year, mon, 0).getDate();
    var mp     = String(mon).padStart(2, '0');
    var result = [];
    for (var d = 1; d <= days; d++) {
      var ds     = String(d);
      var dateStr= year + '-' + mp + '-' + String(d).padStart(2, '0');
      var inSch  = (ssched[ds] || []).indexOf(staffName) >= 0;
      var tkey   = (stimes[ds] || {})[staffName];
      var rkey   = (sreq[staffName] || {})[ds];
      var status, pid;

      if (inSch) {
        if (tkey === 'off') {
          status = 'off';
        } else if (tkey) {
          pid = resolvePid(tkey);
          status = pid !== null ? 'work' : 'off';
        } else {
          status = 'work'; pid = '1';
        }
      } else if (rkey === 'off' || (pub && !inSch)) {
        status = 'off';
      } else {
        status = 'skip';
      }
      result.push({ dateStr: dateStr, status: status, pid: pid });
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
      if (p.status === 'off') {
        setVal(ce, '1'); setVal(se, ''); if (be) setVal(be, '');
      } else {
        setVal(ce, '0'); setVal(se, p.pid || '1'); if (be) setVal(be, p.pid || '1');
      }
      n++;
    });
    return n;
  }

  function clickSave() {
    var sels = [
      'input[type="submit"][value*="保存"]',
      'button[type="submit"]',
      'input[type="submit"]',
    ];
    for (var i = 0; i < sels.length; i++) {
      var el = document.querySelector(sels[i]);
      if (el) { el.click(); return true; }
    }
    return false;
  }

  // ── メイン同期ループ ─────────────────────────────────────────────────
  function runNext() {
    if (_stopped) return;
    var plan = getPlan();
    if (!plan) return;

    var remaining = plan.staffIds.filter(function(id) {
      return plan.done.indexOf(id) < 0;
    });

    if (remaining.length === 0) {
      showUI('✅ 全' + plan.staffIds.length + '名の同期が完了しました！', plan.staffIds.length, plan.staffIds.length);
      setPlan(null);
      sbDelete(TASK_STORE_KEY);
      setTimeout(hideUI, 6000);
      return;
    }

    var total   = plan.staffIds.length;
    var done    = total - remaining.length;
    var nextId  = remaining[0];
    var info    = STAFF_MAP[nextId];
    var name    = info ? info.name : ('ID:' + nextId);
    var shopId  = info ? info.shopId : '';
    var ym      = plan.ym;  // '202606'

    showUI(name + ' を処理中…（残り' + remaining.length + '名）', done, total);

    // 現在ページ確認
    var params = new URLSearchParams(location.search);
    var curStaff = params.get('select_staff');
    var curYm    = params.get('yearmonth');
    var onShiftPage = location.pathname.indexOf('staff_day_shift') >= 0;

    if (onShiftPage && curStaff === nextId && curYm === ym) {
      // 正しいページにいる → 入力して保存
      if (!info) {
        // マッピング不明 → スキップ
        plan.done.push(nextId);
        setPlan(plan);
        runNext();
        return;
      }

      var year = parseInt(ym.slice(0, 4), 10);
      var mon  = parseInt(ym.slice(4, 6), 10);
      var dayPlan = calcPlan(name, year, mon, plan.shiftData);
      var filled  = fillForm(dayPlan);

      showUI(name + ': ' + filled + '日入力、保存中…', done, total);

      setTimeout(function() {
        if (_stopped) return;
        var saved = clickSave();
        if (!saved) {
          // 保存ボタン未発見 → 手動保存を促してスキップ
          showUI('⚠️ ' + name + ': 保存ボタンが見つかりません。手動で保存してください。', done, total);
          setTimeout(function() {
            plan.done.push(nextId);
            setPlan(plan);
            runNext();
          }, 5000);
          return;
        }

        // 保存クリック後: ページリロードか AJAX 完了を待つ
        // ページリロードが起きれば Tampermonkey が再起動してここから続く
        // AJAX なら SAVE_WAIT_MS 後に自動で次へ
        plan.done.push(nextId);
        setPlan(plan);

        setTimeout(function() {
          if (_stopped) return;
          // まだ同じページにいれば AJAX 保存 → 次のスタッフへ移動
          if (location.pathname.indexOf('staff_day_shift') >= 0 &&
              new URLSearchParams(location.search).get('select_staff') === nextId) {
            goNext();
          }
          // ページリロードが起きた場合は window.onload で runNext() が呼ばれる
        }, SAVE_WAIT_MS);
      }, 600);

    } else {
      // 次のスタッフのページへ移動
      goNext();
    }

    function goNext() {
      if (_stopped) return;
      var plan2 = getPlan();
      if (!plan2) return;
      var rem2 = plan2.staffIds.filter(function(id){ return plan2.done.indexOf(id) < 0; });
      if (rem2.length === 0) { runNext(); return; }
      var nId   = rem2[0];
      var nInfo = STAFF_MAP[nId];
      var nShop = nInfo ? nInfo.shopId : '';
      location.href =
        '/client_admin/staff_day_shift.php' +
        '?select_staff=' + encodeURIComponent(nId) +
        (nShop ? '&select_procshop=' + encodeURIComponent(nShop) : '') +
        '&yearmonth=' + encodeURIComponent(plan2.ym);
    }
  }

  // ── 起動処理 ─────────────────────────────────────────────────────────
  function onPageReady() {
    var plan = getPlan();

    if (plan) {
      // ローカルプランあり → 同期を続ける
      showUI('同期を再開しています…', 0, plan.staffIds.length);
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

        // タスクをローカルプランに変換
        var ids = task.staffIds || Object.keys(STAFF_MAP);
        var newPlan = {
          ym:        task.ym,
          shiftData: task.shiftData,
          staffIds:  ids,
          done:      [],
        };
        setPlan(newPlan);

        // 確認ダイアログ
        var year = task.ym.slice(0, 4);
        var mon  = parseInt(task.ym.slice(4, 6), 10);
        var names = ids.map(function(id){ return STAFF_MAP[id] ? STAFF_MAP[id].name : 'ID:'+id; }).join('、');

        if (!confirm(
          '【サムライ SC自動同期】\n\n' +
          year + '年' + mon + '月のシフトを全スタッフ自動入力します。\n\n' +
          '対象(' + ids.length + '名): ' + names + '\n\n' +
          '自動でフォームを入力・保存します。よろしいですか？'
        )) {
          setPlan(null);
          sbDelete(TASK_STORE_KEY);
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
