/* Sundai Bank service agent demo. Vanilla JS, no build, no third-party requests.
   Reads docs/demo/data/{hero,eval,requests}.json. All text goes in with textContent. */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var ORDER = { run: 0, log: 1, review: 2, pause: 3 };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function fmt(v, d) { return isNum(v) ? v.toFixed(d) : 'n/a'; }
  function show(v) {
    if (v === null || v === undefined) return 'none';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
  function load(name) {
    return fetch('demo/data/' + name, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(name + ' ' + r.status);
      return r.json();
    });
  }
  function loadError(host, name) {
    var d = el('p', 'loaderr',
      'Could not load demo/data/' + name + '. Serve the docs folder over http, for example: python3 -m http.server 8765 --directory docs');
    host.appendChild(d);
  }

  /* ================= player ================= */
  var hero = null;
  var S = { key: 'good_legit', idx: 0, reached: 0, resolved: {}, stopped: false, timer: null };

  function steps() { return hero.cases[S.key].steps; }
  function pauseLine() { return isNum(hero.pause_line) ? hero.pause_line : null; }

  function stopPlay() {
    if (S.timer) { clearInterval(S.timer); S.timer = null; }
    var b = $('btn-play');
    if (b) b.textContent = 'Play';
  }

  function reset() {
    stopPlay();
    S.idx = 0; S.reached = 0; S.resolved = {}; S.stopped = false;
    var c = hero.cases[S.key];
    $('msg-who').textContent = c.customer + ' writes in';
    renderMessage(c.message);
    $('player-agent').textContent = c.agent + (c.agent_id ? ', ' + c.agent_id : '');
    render(false);
  }

  function renderMessage(text) {
    var q = $('msg-text');
    clear(q);
    var m = /\[[^\]]*\]/.exec(text || '');
    if (!m) { q.textContent = text || ''; return; }
    q.appendChild(document.createTextNode(text.slice(0, m.index)));
    q.appendChild(el('span', 'inj', m[0]));
    q.appendChild(document.createTextNode(text.slice(m.index + m[0].length)));
  }

  function summarize(params) {
    var keys = ['amount_usd', 'type', 'txn_id', 'field', 'reason', 'resolution', 'reason_code'];
    var out = [];
    keys.forEach(function (k) { if (params && params[k] !== undefined && params[k] !== null) out.push(k + ' ' + params[k]); });
    return out.slice(0, 3).join(', ');
  }

  function paused(s) { return s.decision === 'pause'; }
  function needsGate(i) { var s = steps()[i]; return paused(s) && !S.resolved[s.step]; }

  function render(focusPause) {
    var list = steps();
    var s = list[S.idx];
    var n = list.length;

    /* strip */
    var strip = $('strip');
    clear(strip);
    list.forEach(function (st, i) {
      var b = el('button', 'chip ' + (i > S.reached ? 'unreached' : st.decision));
      b.type = 'button';
      b.disabled = i > S.reached;
      b.setAttribute('aria-label', 'Step ' + st.step + ', ' + st.action + ', decision ' + st.decision);
      if (i === S.idx) b.setAttribute('aria-current', 'step');
      var bar = el('span', 'bar');
      bar.style.height = (6 + (i > S.reached ? 0 : Math.round(Math.min(1, st.risk) * 46))) + 'px';
      b.appendChild(bar);
      b.appendChild(el('span', 'n', String(st.step)));
      b.addEventListener('click', function () { stopPlay(); S.idx = i; render(false); });
      strip.appendChild(b);
    });

    /* head */
    $('st-count').textContent = 'Step ' + s.step + ' of ' + n;
    $('st-action').textContent = s.action;
    var badge = $('st-badge');
    badge.textContent = s.decision;
    badge.className = 'badge ' + s.decision;

    /* what the agent did */
    var kv = $('st-meta');
    clear(kv);
    function row(k, v, warn) {
      kv.appendChild(el('dt', null, k));
      kv.appendChild(el('dd', warn ? 'warn' : null, show(v)));
    }
    row('auth_level', s.auth_level, s.auth_level === 'none' && s.action !== 'read_request' && s.action !== 'verify_identity');
    row('policy_check', s.policy_check, s.policy_check === 'skipped' || s.policy_check === 'failed');
    row('outcome', s.outcome, s.outcome === 'blocked');
    Object.keys(s.params || {}).forEach(function (k) { row(k, s.params[k], false); });
    $('st-why').textContent = s.reasoning_summary ? 'Agent says: ' + s.reasoning_summary : '';

    /* what the risk agent says */
    var pl = pauseLine();
    var fill = $('m-fill');
    fill.style.width = Math.round(Math.min(1, Math.max(0, s.risk)) * 100) + '%';
    fill.className = 'fill' + ((s.decision === 'pause' || s.decision === 'review') ? ' hot' : '');
    $('m-val').textContent = fmt(s.risk, 2);
    $('m-track').setAttribute('aria-label', 'Risk ' + fmt(s.risk, 2) + ' on a scale from 0 to 1');
    var tick = $('m-tick');
    if (pl !== null) {
      tick.hidden = false;
      tick.style.left = Math.round(pl * 100) + '%';
      $('m-tick-lbl').textContent = 'pause line ' + fmt(pl, 2);
    } else { tick.hidden = true; }
    $('s-surprise').textContent = fmt(s.surprise, 2);
    $('s-severity').textContent = fmt(s.severity, 1);
    $('s-decision').textContent = s.decision;
    var fl = $('s-flags');
    clear(fl);
    if (s.flags && s.flags.length) s.flags.forEach(function (f) { fl.appendChild(el('li', null, f)); });
    else fl.appendChild(el('li', 'none', 'No warning flags'));

    /* pause panel */
    var panel = $('pause');
    var gate = paused(s);
    panel.hidden = !gate;
    if (gate) {
      $('pause-h').textContent = 'Paused before step ' + s.step + ': ' + s.action;
      var sum = summarize(s.params);
      var txt = 'The service agent is about to run ' + s.action + (sum ? ' (' + sum + ')' : '') + '. ' +
        'Risk ' + fmt(s.risk, 2) + (pl !== null ? ' is at or above the pause line ' + fmt(pl, 2) : ' triggered the pause') + '. ' +
        'Flags: ' + (s.flags && s.flags.length ? s.flags.join(', ') : 'none') + '. Approve or block?';
      $('pause-text').textContent = txt;
      var dec = S.resolved[s.step];
      var decided = $('pause-decided');
      $('btn-approve').disabled = !!dec;
      $('btn-block').disabled = !!dec;
      $('btn-approve').textContent = 'Approve and run step ' + s.step;
      $('btn-block').textContent = 'Block step ' + s.step;
      if (dec) {
        decided.hidden = false;
        decided.textContent = dec === 'approved'
          ? 'Reviewer approved (demo). Step ' + s.step + ' runs and the session continues.'
          : 'Reviewer blocked (demo). Step ' + s.step + ' does not run and a person takes over the case.';
      } else { decided.hidden = true; decided.textContent = ''; }
      if (focusPause && !dec) $('pause-h').focus();
    }

    /* span */
    var code = $('span-code');
    clear(code);
    var lines = JSON.stringify(s.otel_span || {}, null, 2).split('\n');
    lines.forEach(function (ln) {
      code.appendChild(el('span', ln.indexOf('"gen_ai.') >= 0 ? 'ln hl' : 'ln', ln));
    });

    /* controls and result */
    var atEnd = S.idx === n - 1;
    $('btn-back').disabled = S.idx === 0;
    $('btn-next').disabled = atEnd || S.stopped && S.idx >= S.reached || needsGate(S.idx);
    $('btn-next').textContent = needsGate(S.idx) ? 'Decide first' : 'Next step';
    renderResult(s, atEnd);
  }

  function renderResult(s, atEnd) {
    var box = $('result');
    var list = steps();
    var txt = '';
    var blocked = list.filter(function (x) { return S.resolved[x.step] === 'blocked'; })[0];
    var approved = list.filter(function (x) { return S.resolved[x.step] === 'approved'; })[0];
    if (blocked && S.idx >= list.indexOf(blocked)) {
      txt = 'Result: the risk agent paused before step ' + blocked.step + ' (' + blocked.action + '), a person blocked it, and nothing after it ran.';
    } else if (atEnd) {
      if (approved) txt = 'Result: step ' + approved.step + ' (' + approved.action + ') was paused and a person approved it. The session ran to the end.';
      else {
        var top = list.reduce(function (a, x) { return ORDER[x.decision] > ORDER[a.decision] ? x : a; }, list[0]);
        if (top.decision === 'review') txt = 'Result: no pause. The highest decision was review at step ' + top.step + ' (' + top.action + '): it ran and was flagged for a human look.';
        else if (top.decision === 'log') txt = 'Result: no pause. The highest decision was log, at step ' + top.step + '.';
        else txt = 'Result: no pause. Every step ran.';
      }
    }
    box.hidden = !txt;
    box.textContent = txt;
  }

  function next() {
    if (needsGate(S.idx)) return false;
    if (S.idx >= steps().length - 1) return false;
    if (S.stopped && S.idx >= S.reached) return false;
    S.idx += 1;
    if (S.idx > S.reached) S.reached = S.idx;
    render(true);
    return true;
  }
  function back() { if (S.idx > 0) { stopPlay(); S.idx -= 1; render(false); } }

  function decide(kind) {
    var s = steps()[S.idx];
    S.resolved[s.step] = kind;
    if (kind === 'blocked') {
      S.stopped = true;
      S.reached = S.idx;
    }
    render(false);
    if (kind === 'approved') $('btn-next').focus();
  }

  function play() {
    if (S.timer) { stopPlay(); return; }
    $('btn-play').textContent = 'Stop';
    S.timer = setInterval(function () {
      if (!next() || needsGate(S.idx)) stopPlay();
    }, 1600);
  }

  function initPlayer() {
    var radios = document.querySelectorAll('input[name="case"]');
    Array.prototype.forEach.call(radios, function (r) {
      r.addEventListener('change', function () { if (r.checked) { S.key = r.value; reset(); } });
    });
    $('btn-next').addEventListener('click', next);
    $('btn-back').addEventListener('click', back);
    $('btn-restart').addEventListener('click', reset);
    $('btn-approve').addEventListener('click', function () { decide('approved'); });
    $('btn-block').addEventListener('click', function () { decide('blocked'); });
    if (reduceMotion) $('btn-play').hidden = true; else $('btn-play').addEventListener('click', play);
    $('player').addEventListener('keydown', function (e) {
      if (e.target.closest && e.target.closest('summary, pre, input, textarea')) return;
      if (e.key === 'ArrowRight') { e.preventDefault(); stopPlay(); next(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); back(); }
    });
    var checked = document.querySelector('input[name="case"]:checked');
    S.key = checked ? checked.value : 'good_legit';
    $('sample-hero').hidden = !hero.sample;
    $('real-hero').hidden = !!hero.sample;
    reset();
    renderHistory();
    Array.prototype.forEach.call(radios, function (r) { r.addEventListener('change', renderHistory); });
  }

  /* ================= BehaviorGPT history ================= */
  function renderHistory() {
    if (!hero) return;
    var ol = $('hist');
    clear(ol);
    steps().forEach(function (st) {
      ol.appendChild(el('li', st.decision === 'pause' ? 'bad' : null, 'service.' + st.action));
    });
  }

  /* ================= eval ================= */
  var evalData = null;
  var METRICS = {
    auc: { label: 'AUC', fmt: function (v) { return fmt(v, 3); }, note: 'Higher is better. How well the scores rank risky sessions above normal ones.' },
    catch_rate: { label: 'Catch rate', pct: true, note: 'Higher is better. A pause at or before the first harmful step, at a fixed pause budget on normal sessions.' },
    early_warning: { label: 'Early warning', pct: true, note: 'Higher is better. A log or stronger decision before the harmful step.' },
    false_alarm_rate: { label: 'False alarms', pct: true, note: 'Lower is better. Share of normal sessions that were paused.' },
    lead_time_steps: { label: 'Lead time', fmt: function (v) { return fmt(v, 1) + ' steps'; }, note: 'Higher is better. How many steps before the harmful step the meter rose.' }
  };
  function pct(v) {
    if (!isNum(v)) return 'n/a';
    return (v > 0 && v < 0.1 ? (v * 100).toFixed(1) : String(Math.round(v * 100))) + '%';
  }
  function metricText(key, v) {
    var m = METRICS[key];
    return m.pct ? pct(v) : m.fmt(v);
  }
  function isBase(r) { return !r.group || r.order === 0 || /severity|baseline|surprise only/i.test(r.name || ''); }
  function gClass(r) { return isBase(r) ? 'g-base' : 'g-' + r.group; }

  function renderEvalTable() {
    var tb = $('eval-body');
    clear(tb);
    evalData.rows.forEach(function (r) {
      var tr = el('tr');
      tr.appendChild(el('td', null, r.name));
      tr.appendChild(el('td', null, show(r.group)));
      tr.appendChild(el('td', null, r.order ? String(r.order) : 'n/a'));
      tr.appendChild(el('td', null, fmt(r.auc, 3)));
      tr.appendChild(el('td', null, pct(r.catch_rate)));
      tr.appendChild(el('td', null, pct(r.early_warning)));
      tr.appendChild(el('td', null, pct(r.false_alarm_rate)));
      tr.appendChild(el('td', null, fmt(r.lead_time_steps, 1)));
      tb.appendChild(tr);
    });
    var meta = $('eval-meta');
    if (evalData.sample) meta.textContent = '';
    else {
      var seeds = (evalData.seeds || []).length;
      meta.textContent = seeds + ' seeds, ' + evalData.sessions_per_seed + ' sessions per seed' + (evalData.synthetic ? ', synthetic data' : '') + '. Labels sealed until scoring. Rates are means over seeds.';
    }
  }

  function renderBars() {
    var key = (document.querySelector('input[name="metric"]:checked') || {}).value || 'catch_rate';
    var ol = $('bars');
    clear(ol);
    var vals = evalData.rows.map(function (r) { return r[key]; }).filter(isNum);
    var max = METRICS[key].pct || key === 'auc' ? 1 : Math.max.apply(null, vals.concat([0.0001]));
    evalData.rows.forEach(function (r) {
      var li = el('li', gClass(r));
      var top = el('div', 'row-top');
      top.appendChild(el('span', null, r.name));
      top.appendChild(el('span', null, metricText(key, r[key])));
      var bt = el('div', 'bt');
      var bf = el('div', 'bf');
      bf.style.width = isNum(r[key]) ? Math.round(Math.max(0, Math.min(1, r[key] / max)) * 100) + '%' : '0%';
      bt.appendChild(bf);
      li.appendChild(top);
      li.appendChild(bt);
      ol.appendChild(li);
    });
    $('bars-note').textContent = METRICS[key].note + (evalData.sample ? ' Illustrative sample, not measured.' : '');
  }

  function renderDrop() {
    var rows = evalData.rows;
    function pick(g) {
      return rows.filter(function (r) { return r.group === g && !isBase(r) && r.order === 2; })[0] ||
             rows.filter(function (r) { return r.group === g && !isBase(r); })[0];
    }
    var a = pick('G0'), b = pick('G3');
    var p = $('drop-line');
    if (a && b && isNum(a.catch_rate) && isNum(b.catch_rate)) {
      p.textContent = 'From G0 to G3, the catch rate goes from ' + pct(a.catch_rate) + ' to ' + pct(b.catch_rate) +
        ', and the false alarm rate goes from ' + pct(a.false_alarm_rate) + ' to ' + pct(b.false_alarm_rate) +
        '. That gap is the answer to which metadata the risk side needs.' +
        (evalData.sample ? ' (Illustrative sample.)' : '');
    } else p.textContent = '';
  }

  function renderBgpt() {
    var row = evalData.rows.filter(function (r) { return /behaviorgpt/i.test(r.name || ''); })[0];
    var box = $('bgpt-status');
    var cmd = $('bgpt-cmd');
    if (row) {
      box.className = 'note';
      box.textContent = 'A BehaviorGPT row exists in the data: AUC ' + fmt(row.auc, 3) + ', catch rate ' + pct(row.catch_rate) +
        ', false alarms ' + pct(row.false_alarm_rate) + '. It is a separate row, never blended with the baseline.';
      cmd.hidden = true;
    } else {
      box.className = 'note copper';
      box.textContent = 'There is no BehaviorGPT row in the data yet, so this page shows no BehaviorGPT result and claims nothing about it.';
      cmd.hidden = false;
    }
  }

  function initEval() {
    $('sample-eval').hidden = !evalData.sample;
    renderEvalTable();
    renderBars();
    renderDrop();
    renderBgpt();
    Array.prototype.forEach.call(document.querySelectorAll('input[name="metric"]'), function (r) {
      r.addEventListener('change', renderBars);
    });
  }

  /* ================= requests ================= */
  function initRequests(data) {
    $('sample-req').hidden = !data.sample;
    $('real-req').hidden = !!data.sample;
    var ul = $('req-list');
    clear(ul);
    (data.requests || []).forEach(function (r) {
      var li = el('li', null, r.text);
      li.appendChild(el('span', 'intent', r.intent + ' · ' + r.source));
      ul.appendChild(li);
    });
  }

  /* ================= boot ================= */
  load('hero.json').then(function (d) { hero = d; initPlayer(); })
    .catch(function () { loadError($('player'), 'hero.json'); });
  load('eval.json').then(function (d) { evalData = d; initEval(); if (hero) renderHistory(); })
    .catch(function () { loadError($('bars'), 'eval.json'); });
  load('requests.json').then(initRequests)
    .catch(function () { loadError($('req-list'), 'requests.json'); });
})();
