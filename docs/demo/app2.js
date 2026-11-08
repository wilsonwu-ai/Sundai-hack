/* Sundai Bank demo, part 2: agent 10 (network watch) and fleet risk.
   Reads demo/data/{net-hero,net-eval,toolguard,fleet}.json, falling back to demo/data/samples/ when a real file is not there.
   All text goes in with textContent. */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var RANK = { allow: 0, run: 0, log: 1, contain: 2, review: 2, page_it: 3, pause: 3 };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function fmt(v, d) { return isNum(v) ? v.toFixed(d) : 'n/a'; }
  function pct(v) {
    if (!isNum(v)) return 'n/a';
    return (v > 0 && v < 0.1 ? (v * 100).toFixed(1) : String(Math.round(v * 100))) + '%';
  }
  function usd(v) { return isNum(v) ? '$' + Math.round(v).toLocaleString('en-US') : 'n/a'; }
  function show(v) {
    if (v === null || v === undefined) return 'none';
    if (Array.isArray(v)) return v.length ? v.join(', ') : 'none';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
  function words(d) { return String(d).replace(/_/g, ' '); }
  function fetchJson(path) {
    return fetch(path, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(path + ' ' + r.status);
      return r.json();
    });
  }
  function loadFB(name) {
    return fetchJson('demo/data/' + name).catch(function () { return fetchJson('demo/data/samples/' + name); });
  }
  function loadError(host, name) {
    host.appendChild(el('p', 'loaderr', 'Could not load demo/data/' + name +
      '. Serve the docs folder over http, for example: python3 -m http.server 8765 --directory docs'));
  }
  function chipClass(d) {
    if (d === 'page_it' || d === 'pause') return 'pause';
    if (d === 'contain' || d === 'review') return 'review';
    if (d === 'log') return 'log';
    return 'run';
  }

  /* ================= agent 10 player ================= */
  var net = null;
  var N = { key: 'good_normal', track: 'req', st: {}, timer: null };

  function tstate() { return N.st[N.track]; }
  function resetStates() {
    N.st = { req: { idx: 0, reached: 0, resolved: {}, stopped: false }, ag: { idx: 0, reached: 0, resolved: {}, stopped: false } };
  }
  function curCase() { return net.cases[N.key]; }
  function items() { var c = curCase(); return N.track === 'req' ? c.requests : c.agent_steps; }
  function idOf(it) { return N.track === 'req' ? it.request_index : it.step; }
  function gateWord() { return N.track === 'req' ? 'page_it' : 'pause'; }
  function isGate(it) { return it.decision === gateWord(); }
  function needsGate(i) { var it = items()[i]; return isGate(it) && !tstate().resolved[idOf(it)]; }
  function lineOf() {
    var v = N.track === 'req' ? net.pause_line : net.agent_pause_line;
    return isNum(v) ? v : null;
  }
  function stopPlay() {
    if (N.timer) { clearInterval(N.timer); N.timer = null; }
    $('n-play').textContent = 'Play';
  }

  function nReset() {
    stopPlay();
    resetStates();
    var c = curCase();
    $('n-who').textContent = c.client;
    var note = $('n-note');
    clear(note);
    var ua = '';
    (c.requests || []).forEach(function (r) {
      var u = r.context && r.context.user_agent;
      if (!ua && u) ua = u;
    });
    if (ua) {
      note.appendChild(document.createTextNode('The request header carries text for the security agent: '));
      var m = /\[[^\]]*\]/.exec(ua);
      if (m) {
        note.appendChild(el('span', 'inj', m[0]));
      } else { note.appendChild(el('span', 'inj', ua)); }
      note.appendChild(document.createTextNode(' The detector never reads it; it only sees a payload flag.'));
    } else {
      note.textContent = 'Ordinary requests from a known device. Nothing in the request headers addresses the security agent.';
    }
    $('n-agent').textContent = c.agent + (c.agent_id ? ', ' + c.agent_id : '');
    nRender(false);
  }

  function warnKey(k, v) {
    if (/_new_for_subject$/.test(k) && v === true) return true;
    if (k === 'object_owner_match' && v === false) return true;
    if (k === 'payload_flags' && Array.isArray(v) && v.length) return true;
    if (k === 'mfa_level' && v === 'none') return true;
    return false;
  }

  function nRender(focusPause) {
    var list = items();
    var S = tstate();
    var it = list[S.idx];
    var n = list.length;
    var isReq = N.track === 'req';
    var line = lineOf();

    $('tab-req').setAttribute('aria-pressed', String(isReq));
    $('tab-ag').setAttribute('aria-pressed', String(!isReq));

    var strip = $('n-strip');
    clear(strip);
    list.forEach(function (x, i) {
      var b = el('button', 'chip ' + (i > S.reached ? 'unreached' : chipClass(x.decision)));
      b.type = 'button';
      b.disabled = i > S.reached;
      b.setAttribute('aria-label', (isReq ? 'Request ' : 'Step ') + (i + 1) + ', ' + (isReq ? x.method + ' ' + x.route : x.action) + ', decision ' + words(x.decision));
      if (i === S.idx) b.setAttribute('aria-current', 'step');
      var bar = el('span', 'bar');
      bar.style.height = (6 + (i > S.reached ? 0 : Math.round(Math.min(1, x.risk || 0) * 46))) + 'px';
      b.appendChild(bar);
      b.appendChild(el('span', 'n', String(i + 1)));
      b.addEventListener('click', function () { stopPlay(); S.idx = i; nRender(false); });
      strip.appendChild(b);
    });

    $('n-count').textContent = (isReq ? 'Request ' : 'Step ') + (S.idx + 1) + ' of ' + n;
    $('n-action').textContent = isReq ? it.method + ' ' + it.route : it.action;
    var badge = $('n-badge');
    badge.textContent = words(it.decision);
    badge.className = 'badge ' + chipClass(it.decision);
    $('n-left-h').textContent = isReq ? 'What was requested' : 'What agent 10 did';
    $('n-right-h').textContent = isReq ? 'What the network detector says' : 'What the behaviour layer says';
    $('n-exp-box').hidden = !isReq;

    var kv = $('n-meta');
    clear(kv);
    function row(k, v, warn) {
      kv.appendChild(el('dt', null, k));
      kv.appendChild(el('dd', warn ? 'warn' : null, show(v)));
    }
    if (isReq) {
      row('status', it.status, isNum(it.status) && it.status >= 400);
      row('asset', it.asset, false);
      var ctx = it.context || {};
      Object.keys(ctx).forEach(function (k) {
        if (k === 'user_agent') return;
        if (ctx[k] === null || ctx[k] === undefined) return;
        row(k, ctx[k], warnKey(k, ctx[k]));
      });
      $('n-why').textContent = '';
    } else {
      row('auth_level', it.auth_level, false);
      row('policy_check', it.policy_check, it.policy_check === 'skipped' || it.policy_check === 'failed');
      row('outcome', it.outcome, it.outcome === 'blocked');
      Object.keys(it.params || {}).forEach(function (k) { row(k, it.params[k], false); });
      $('n-why').textContent = it.reasoning_summary ? 'Agent says: ' + it.reasoning_summary : '';
    }

    var fill = $('n-mfill');
    fill.style.width = Math.round(Math.min(1, Math.max(0, it.risk || 0)) * 100) + '%';
    fill.className = 'fill' + (RANK[it.decision] >= 2 ? ' hot' : '');
    $('n-mval').textContent = fmt(it.risk, 2);
    $('n-mtrack').setAttribute('aria-label', 'Risk ' + fmt(it.risk, 2) + ' on a scale from 0 to 1');
    var tick = $('n-mtick');
    if (line !== null) {
      tick.hidden = false;
      tick.style.left = Math.round(line * 100) + '%';
      $('n-mtick-lbl').textContent = 'pause line ' + fmt(line, 2);
    } else { tick.hidden = true; }
    $('n-sur').textContent = fmt(it.surprise, 2);
    $('n-sev').textContent = fmt(it.severity, 1);
    $('n-exp').textContent = usd(it.exposure_usd);
    $('n-dec').textContent = words(it.decision);
    var fl = $('n-flags');
    clear(fl);
    if (it.flags && it.flags.length) it.flags.forEach(function (f) { fl.appendChild(el('li', null, f)); });
    else fl.appendChild(el('li', 'none', 'No warning flags'));
    var notes = {
      allow: 'Served.', log: 'Served and logged for later review.',
      contain: 'Contained automatically (throttle, step up or revoke), logged, nobody paged.',
      page_it: 'A person on the IT team is paged before this is served.',
      run: 'Runs.', review: 'Runs and is flagged for a human look.', pause: 'Paused before it runs, for a person to decide.'
    };
    var dn = notes[it.decision] || '';
    if (isReq && isNum(it.value_at_risk_usd)) dn += ' Exposure is the value at risk (' + usd(it.value_at_risk_usd) + ') times the risk.';
    $('n-dec-note').textContent = dn;

    var panel = $('n-pause');
    var gate = isGate(it);
    panel.hidden = !gate;
    if (gate) {
      var key = idOf(it);
      var dec = S.resolved[key];
      if (isReq) {
        $('n-pause-h').textContent = 'IT is paged before request ' + (S.idx + 1) + ': ' + it.method + ' ' + it.route;
        $('n-pause-text').textContent = 'The request would be served. Risk ' + fmt(it.risk, 2) +
          (line !== null ? ' is at or above the line ' + fmt(line, 2) : ' triggered the page') + '. Exposure ' + usd(it.exposure_usd) +
          '. Flags: ' + (it.flags && it.flags.length ? it.flags.join(', ') : 'none') + '. Approve or block?';
      } else {
        $('n-pause-h').textContent = 'Paused before step ' + (S.idx + 1) + ': ' + it.action;
        $('n-pause-text').textContent = 'Agent 10 is about to run ' + it.action + '. Risk ' + fmt(it.risk, 2) +
          (line !== null ? ' is at or above the pause line ' + fmt(line, 2) : ' triggered the pause') +
          '. Flags: ' + (it.flags && it.flags.length ? it.flags.join(', ') : 'none') + '. Approve or block?';
      }
      $('n-approve').disabled = !!dec;
      $('n-block').disabled = !!dec;
      $('n-approve').textContent = 'Approve ' + (isReq ? 'request ' : 'step ') + (S.idx + 1);
      $('n-block').textContent = 'Block ' + (isReq ? 'request ' : 'step ') + (S.idx + 1);
      var decided = $('n-decided');
      if (dec) {
        decided.hidden = false;
        decided.textContent = dec === 'approved'
          ? 'Reviewer approved (demo). It runs and the session continues.'
          : 'Reviewer blocked (demo). It does not run and a person takes over.';
      } else { decided.hidden = true; decided.textContent = ''; }
      if (focusPause && !dec) $('n-pause-h').focus();
    }

    $('n-span-sum').textContent = isReq ? 'OpenTelemetry HTTP span for this request' : 'OpenTelemetry span for this action';
    $('n-span-cap').textContent = isReq
      ? 'One server span per request, named by method and route. The http.* lines are the standard attributes; the bank.* lines are what the gateway knows. Raw request content is not in the span, only a payload flag.'
      : 'One span per action. The gen_ai.* lines follow the OpenTelemetry GenAI conventions. No customer or request text is in it.';
    var code = $('n-span-code');
    clear(code);
    JSON.stringify(it.otel_span || {}, null, 2).split('\n').forEach(function (ln) {
      var hl = ln.indexOf('"gen_ai.') >= 0 || ln.indexOf('"http.') >= 0;
      code.appendChild(el('span', hl ? 'ln hl' : 'ln', ln));
    });

    var atEnd = S.idx === n - 1;
    $('n-back').disabled = S.idx === 0;
    $('n-next').disabled = atEnd || (S.stopped && S.idx >= S.reached) || needsGate(S.idx);
    $('n-next').textContent = needsGate(S.idx) ? 'Decide first' : 'Next step';
    nResult(atEnd);
  }

  function nResult(atEnd) {
    var S = tstate();
    var list = items();
    var box = $('n-result');
    var txt = '';
    var blocked = list.filter(function (x) { return S.resolved[idOf(x)] === 'blocked'; })[0];
    var approved = list.filter(function (x) { return S.resolved[idOf(x)] === 'approved'; })[0];
    var label = function (x, i) { return (N.track === 'req' ? 'request ' : 'step ') + (i + 1) + ' (' + (N.track === 'req' ? x.method + ' ' + x.route : x.action) + ')'; };
    if (blocked && S.idx >= list.indexOf(blocked)) {
      txt = 'Result: the risk side stopped ' + label(blocked, list.indexOf(blocked)) + ', a person blocked it, and nothing after it ran.';
    } else if (atEnd) {
      if (approved) txt = 'Result: ' + label(approved, list.indexOf(approved)) + ' was stopped for a person, who approved it. The session ran to the end.';
      else {
        var top = list.reduce(function (a, x) { return RANK[x.decision] > RANK[a.decision] ? x : a; }, list[0]);
        var ti = list.indexOf(top);
        if (RANK[top.decision] === 3) txt = '';
        else if (RANK[top.decision] === 0) txt = 'Result: nothing was stopped. Every ' + (N.track === 'req' ? 'request' : 'step') + ' ran.';
        else if (top.decision === 'contain') txt = 'Result: nobody was paged. The highest decision was contain at ' + label(top, ti) + ': handled automatically and logged.';
        else if (top.decision === 'review') txt = 'Result: nothing was paused. The highest decision was review at ' + label(top, ti) + ': it ran and was flagged for a human look.';
        else txt = 'Result: nothing was paused. The highest decision was log at ' + label(top, ti) + '.';
      }
    }
    box.hidden = !txt;
    box.textContent = txt;
  }

  function nNext() {
    var S = tstate();
    if (needsGate(S.idx) || S.idx >= items().length - 1 || (S.stopped && S.idx >= S.reached)) return false;
    S.idx += 1;
    if (S.idx > S.reached) S.reached = S.idx;
    nRender(true);
    return true;
  }
  function nBack() { var S = tstate(); if (S.idx > 0) { stopPlay(); S.idx -= 1; nRender(false); } }
  function nDecide(kind) {
    var S = tstate();
    var it = items()[S.idx];
    S.resolved[idOf(it)] = kind;
    if (kind === 'blocked') { S.stopped = true; S.reached = S.idx; }
    nRender(false);
    if (kind === 'approved') $('n-next').focus();
  }
  function nPlay() {
    if (N.timer) { stopPlay(); return; }
    $('n-play').textContent = 'Stop';
    N.timer = setInterval(function () { if (!nNext() || needsGate(tstate().idx)) stopPlay(); }, 1600);
  }
  function setTrack(t) { stopPlay(); N.track = t; nRender(false); }

  function initNet() {
    $('sample-net').hidden = !net.sample;
    var f = $('fronts');
    if (isNum(net.value_fronted_usd)) {
      var v = net.value_fronted_usd;
      var m = v / 1e6;
      f.textContent = 'This API fronts about $' + (m >= 10 ? Math.round(m) : m.toFixed(1)) + ' million' + (net.values_note ? ', ' + net.values_note : '') + '.';
      f.hidden = false;
    }
    Array.prototype.forEach.call(document.querySelectorAll('input[name="ncase"]'), function (r) {
      r.addEventListener('change', function () { if (r.checked) { N.key = r.value; nReset(); } });
    });
    var checked = document.querySelector('input[name="ncase"]:checked');
    N.key = checked ? checked.value : 'good_normal';
    $('tab-req').addEventListener('click', function () { setTrack('req'); });
    $('tab-ag').addEventListener('click', function () { setTrack('ag'); });
    $('n-next').addEventListener('click', nNext);
    $('n-back').addEventListener('click', nBack);
    $('n-restart').addEventListener('click', nReset);
    $('n-approve').addEventListener('click', function () { nDecide('approved'); });
    $('n-block').addEventListener('click', function () { nDecide('blocked'); });
    if (reduceMotion) $('n-play').hidden = true; else $('n-play').addEventListener('click', nPlay);
    $('nplayer').addEventListener('keydown', function (e) {
      if (e.target.closest && e.target.closest('summary, pre, input, textarea')) return;
      if (e.key === 'ArrowRight') { e.preventDefault(); stopPlay(); nNext(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); nBack(); }
    });
    nReset();
    renderTriage();
  }

  /* ================= triage table ================= */
  function renderTriage() {
    var tb = $('triage-body');
    clear(tb);
    var rows = net.triage || [];
    var pages = 0;
    rows.forEach(function (r) {
      var tr = el('tr');
      tr.appendChild(el('td', null, r.title));
      tr.appendChild(el('td', null, r.asset));
      tr.appendChild(el('td', null, usd(r.value_at_risk_usd)));
      tr.appendChild(el('td', null, fmt(r.peak_risk, 2)));
      tr.appendChild(el('td', null, usd(r.exposure_usd)));
      var td = el('td');
      td.appendChild(el('span', 'badge ' + chipClass(r.decision), words(r.decision)));
      tr.appendChild(td);
      if (r.decision === 'page_it') pages += 1;
      tb.appendChild(tr);
    });
    var contained = rows.filter(function (r) { return r.decision === 'contain'; }).length;
    $('triage-note').textContent = rows.length
      ? pages + ' of ' + rows.length + ' use cases page IT. ' + contained + ' are contained automatically and logged, with nobody paged.' +
        (net.sample ? ' Illustrative sample.' : '')
      : '';
  }

  /* ================= network evaluation ================= */
  var neval = null;
  var NM = {
    catch_rate: { pct: true, note: 'Higher is better. Paused or contained at or before the harmful request.' },
    auc: { fmt: function (v) { return fmt(v, 3); }, note: 'Higher is better. How well the scores rank attack sessions above normal ones.' },
    early_warning: { pct: true, note: 'Higher is better. A log or stronger decision before the harmful request.' },
    false_alarm_rate: { pct: true, note: 'Lower is better. Share of normal sessions that were paused or contained.' },
    aggregator_false_alarm_rate: { pct: true, note: 'Lower is better. The aggregator is high volume, regular and legitimate: the false alarm trap.' }
  };
  function nmText(k, v) { return NM[k].pct ? pct(v) : NM[k].fmt(v); }
  function nIsBase(r) { return !r.group; }
  function nClass(r) { return nIsBase(r) ? 'g-base' : 'g-' + r.group; }

  function renderNevalTable() {
    var tb = $('neval-body');
    clear(tb);
    neval.rows.forEach(function (r) {
      var tr = el('tr');
      tr.appendChild(el('td', null, r.name));
      tr.appendChild(el('td', null, show(r.group)));
      tr.appendChild(el('td', null, r.order ? String(r.order) : 'n/a'));
      tr.appendChild(el('td', null, fmt(r.auc, 3)));
      tr.appendChild(el('td', null, pct(r.catch_rate)));
      tr.appendChild(el('td', null, pct(r.early_warning)));
      tr.appendChild(el('td', null, pct(r.false_alarm_rate)));
      tr.appendChild(el('td', null, pct(r.aggregator_false_alarm_rate)));
      tr.appendChild(el('td', null, fmt(r.lead_time_requests, 1)));
      tb.appendChild(tr);
    });
    $('neval-meta').textContent = neval.sample ? '' :
      (neval.seeds || []).length + ' seeds, ' + neval.sessions_per_seed + ' sessions per seed' + (neval.synthetic ? ', synthetic data' : '') + '. Rates are means over seeds.';
  }

  function renderNbars() {
    var key = (document.querySelector('input[name="nmetric"]:checked') || {}).value || 'catch_rate';
    var ol = $('nbars');
    clear(ol);
    neval.rows.forEach(function (r) {
      var li = el('li', nClass(r));
      var top = el('div', 'row-top');
      top.appendChild(el('span', null, r.name));
      top.appendChild(el('span', null, nmText(key, r[key])));
      var bt = el('div', 'bt');
      var bf = el('div', 'bf');
      bf.style.width = isNum(r[key]) ? Math.round(Math.max(0, Math.min(1, r[key])) * 100) + '%' : '0%';
      bt.appendChild(bf);
      li.appendChild(top);
      li.appendChild(bt);
      ol.appendChild(li);
    });
    $('nbars-note').textContent = NM[key].note + (neval.sample ? ' Illustrative sample, not measured.' : '');
  }

  function metricCard(title, big, lines, bars) {
    var c = el('div', 'card');
    c.appendChild(el('h3', null, title));
    c.appendChild(el('p', 'bigline small-big', big));
    lines.forEach(function (l) { c.appendChild(el('p', 'small', l)); });
    var ol = el('ol', 'bars mini');
    var max = Math.max.apply(null, bars.map(function (b) { return isNum(b[1]) ? b[1] : 0; }).concat([0.0001]));
    bars.forEach(function (b) {
      var li = el('li', b[2] || 'g-G0');
      var top = el('div', 'row-top');
      top.appendChild(el('span', null, b[0]));
      top.appendChild(el('span', null, b[3]));
      var bt = el('div', 'bt');
      var bf = el('div', 'bf');
      bf.style.width = Math.round((isNum(b[1]) ? b[1] : 0) / max * 100) + '%';
      bt.appendChild(bf);
      li.appendChild(top);
      li.appendChild(bt);
      ol.appendChild(li);
    });
    c.appendChild(ol);
    return c;
  }

  function renderTriageMetrics() {
    var host = $('triage-metrics');
    clear(host);
    var t = neval.triage || {};
    var a = neval.agent10 || {};
    var pagesNow = t.it_pages_per_1000, base = t.baseline_pages_per_1000;
    var avoided = isNum(t.pages_avoided_per_1000) ? t.pages_avoided_per_1000 : (isNum(pagesNow) && isNum(base) ? base - pagesNow : null);
    if (isNum(pagesNow) && isNum(base)) {
      host.appendChild(metricCard('IT pages avoided', String(Math.round(avoided)) + ' fewer pages per 1,000 sessions',
        ['With triage, IT is paged ' + Math.round(pagesNow) + ' times per 1,000 sessions. Paging on every alert would be ' + Math.round(base) + '.'],
        [['Page on every alert', base, 'g-base', String(Math.round(base))], ['With dollar triage', pagesNow, 'g-N2', String(Math.round(pagesNow))]]));
    }
    if (isNum(t.dollars_stopped_usd) || isNum(t.dollars_missed_usd)) {
      host.appendChild(metricCard('Dollars stopped before harm', usd(t.dollars_stopped_usd) + ' stopped, ' + usd(t.dollars_missed_usd) + ' missed',
        ['Exposure paused or contained at or before the harmful request, against exposure that got through.'],
        [['Stopped before harm', t.dollars_stopped_usd, 'g-N2', usd(t.dollars_stopped_usd)], ['Missed', t.dollars_missed_usd, 'g-N3', usd(t.dollars_missed_usd)]]));
    }
    var note = [];
    if (isNum(a.agent_auc)) note.push('Agent 10 itself: the behaviour scorer separates good from bad netsec agents with an agent AUC of ' + fmt(a.agent_auc, 3) + '.');
    var bp = a.bad_harmful_steps_paused;
    if (bp && isNum(bp.k) && isNum(bp.n)) note.push('It paused ' + bp.k + ' of ' + bp.n + ' of the bad agent\'s harmful steps.');
    if (neval.sample) note.push('Illustrative sample, not measured.');
    $('a10-metrics-note').textContent = note.join(' ');
  }

  function initNeval() {
    $('sample-neval').hidden = !neval.sample;
    renderNevalTable();
    renderNbars();
    renderTriageMetrics();
    Array.prototype.forEach.call(document.querySelectorAll('input[name="nmetric"]'), function (r) {
      r.addEventListener('change', renderNbars);
    });
  }

  /* ================= fleet ================= */
  function exists(url) {
    return fetch(url, { method: 'HEAD', cache: 'no-cache' }).then(function (r) { return r.ok; }, function () { return false; });
  }

  function mediaInto(id, build) {
    var host = $(id);
    var cap = host.querySelector('figcaption');
    return build(host, cap);
  }

  function initFleetMedia(sample) {
    var tag = sample ? ' (illustrative sample)' : '';
    function put(id, make) {
      var host = $(id);
      var ph = host.querySelector('.ph');
      if (ph) host.removeChild(ph);
      host.appendChild(make());
      var cap = host.querySelector('figcaption');
      if (cap && sample) cap.textContent = cap.textContent + tag;
    }
    function missing(id) { /* placeholder text stays */ }
    exists('fleet/fleet-diagram.svg').then(function (ok) {
      if (!ok) return missing('m-diagram');
      put('m-diagram', function () {
        var i = el('img'); i.src = 'fleet/fleet-diagram.svg'; i.alt = 'Diagram of the fleet risk method: score every agent, test against the pause budget, flag the risky ones'; i.loading = 'lazy'; return i;
      });
    });
    exists('fleet/explainer.png').then(function (ok) {
      if (!ok) return missing('m-explainer');
      put('m-explainer', function () {
        var i = el('img'); i.src = 'fleet/explainer.png'; i.alt = 'Picture explainer of how the fleet risk answer is produced'; i.loading = 'lazy'; return i;
      });
    });
    Promise.all([exists('fleet/motion/fleet-risk.mp4'), exists('fleet/motion/poster.png')]).then(function (r) {
      if (!r[0]) return missing('m-video');
      put('m-video', function () {
        var v = el('video');
        v.controls = true; v.muted = true; v.setAttribute('muted', ''); v.preload = 'none'; v.setAttribute('playsinline', '');
        if (r[1]) v.poster = 'fleet/motion/poster.png';
        v.setAttribute('aria-label', 'Fleet risk video, about 15 seconds');
        var s = el('source'); s.src = 'fleet/motion/fleet-risk.mp4'; s.type = 'video/mp4';
        v.appendChild(s);
        return v;
      });
    });
  }

  function initFleet(f) {
    $('sample-fleet').hidden = !f.sample;
    var fl = f.fleet || {};
    $('fleet-head').textContent = pct(fl.risky_share) + ' of our agents are risky (' + fl.risky + ' of ' + fl.agents + ')';
    $('fleet-rule').textContent = f.rule ? 'Risky means: ' + f.rule + '.' : '';

    var cats = (f.categories || []);
    var stand = f.stand_in_categories || [];
    var live = cats.filter(function (c) { return c.source !== 'missing' && c.agents > 0; });
    var max = Math.max.apply(null, live.map(function (c) { return c.risky_share || 0; }).concat([0.0001]));
    var top = live.slice().sort(function (a, b) { return (b.risky_share || 0) - (a.risky_share || 0); })[0];
    $('fleet-where').textContent = (top ? 'The highest share is in ' + top.agent_type + ': ' + pct(top.risky_share) + ' (' + top.risky + ' of ' + top.agents + '). ' : '') +
      'Bars are scaled to the highest category. Hatched bars are stand-ins: a simulator, not the team\'s logs.';
    var ol = $('fleet-cats');
    clear(ol);
    cats.forEach(function (c) {
      var isMiss = c.source === 'missing' || !c.agents;
      var isStand = !isMiss && (c.source === 'stand_in' || stand.indexOf(c.agent_type) >= 0);
      var li = el('li', 'cat ' + (isMiss ? 'miss' : isStand ? 'stand' : 'real'));
      var top = el('div', 'row-top');
      var name = el('span', null, c.agent_type);
      if (isStand) name.appendChild(el('em', 'tagx', 'stand-in'));
      top.appendChild(name);
      top.appendChild(el('span', null, isMiss ? 'no logs yet' : pct(c.risky_share) + ', ' + c.risky + ' of ' + c.agents));
      var bt = el('div', 'bt');
      var bf = el('div', 'bf');
      bf.style.width = isMiss ? '0%' : Math.round((c.risky_share || 0) / max * 100) + '%';
      bt.appendChild(bf);
      li.appendChild(top);
      li.appendChild(bt);
      ol.appendChild(li);
    });

    var w = f.worst || {};
    var wa = (f.agents || []).filter(function (a) { return a.agent_id === w.agent_id; })[0];
    var host = $('fleet-worst');
    clear(host);
    if (w.agent_id) {
      host.appendChild(el('span', 'tag', 'Worst agent'));
      host.appendChild(el('h3', null, w.agent_id + ' (' + w.agent_type + ')'));
      if (wa) host.appendChild(el('p', null, 'Paused in ' + wa.paused + ' of ' + wa.sessions + ' sessions (' + pct(wa.paused_share) + '), against a ' + Math.round((f.budget || 0.05) * 100) + '% budget.'));
      host.appendChild(el('p', 'small', 'Why, in three reasons:'));
      var rl = el('ol', 'reasons');
      (w.why || []).forEach(function (t) { rl.appendChild(el('li', null, t)); });
      host.appendChild(rl);
      if (wa && wa.examples && wa.examples.length) {
        host.appendChild(el('p', 'small', 'Example paused steps (no customer text is kept):'));
        var ul = el('ul', 'ex');
        wa.examples.slice(0, 3).forEach(function (e) {
          ul.appendChild(el('li', null, e.session_id + ', step ' + e.step + ', ' + e.action + ', risk ' + fmt(e.risk, 2) + ': ' + e.reason));
        });
        host.appendChild(ul);
      }
    } else host.appendChild(el('p', null, 'No agent crossed the risky line.'));

    var r = f.reveal || {};
    $('fleet-reveal').textContent = 'We opened the sealed answers: ' + r.flagged_and_weaker + ' of ' + r.flagged + ' flagged agents really were the weaker ones, and ' +
      pct(r.recall) + ' of the weaker agents were caught (' + r.weaker_total + ' in all).' + (f.sample ? ' Illustrative sample.' : '');

    var code = $('fleet-code');
    clear(code);
    (f.code || []).forEach(function (c) {
      var wrap = el('div', 'codeblock');
      var head = el('div', 'codehead');
      head.appendChild(el('b', null, c.what));
      head.appendChild(el('code', null, c.file + ':' + c.start + '-' + c.end));
      wrap.appendChild(head);
      var pre = el('pre', 'codepre');
      pre.tabIndex = 0;
      var lines = String(c.snippet || '').replace(/\n+$/, '').split('\n');
      lines.forEach(function (ln, i) {
        var row = el('span', 'cl');
        row.appendChild(el('span', 'no', String((c.start || 1) + i)));
        row.appendChild(el('span', 'tx', ln));
        pre.appendChild(row);
      });
      wrap.appendChild(pre);
      code.appendChild(wrap);
    });
    initFleetMedia(!!f.sample);
  }

  /* ================= third layer: tool-call guard ================= */
  var TG_CASE_TITLES = {
    hero_legit: 'Hero, legitimate: the KYC agent verifies a new customer',
    hero_attack: 'Hero, attack: a customer message carries a planted instruction and the service agent posts that SSN outside',
    bot_campaign: 'A bot runs the same planted instruction across many sessions',
    spoofed_agent: 'A bot posing as the KYC agent',
    key_exfil: 'An agent reads a deploy key and posts it out',
    slow_drip_lookalike: 'Slow drip to a lookalike host',
    email_not_on_file: 'An agent emails a statement to an address not on file'
  };
  var TG_ACTOR = { person: 'A person', bot: 'A bot', none: 'Nobody hostile' };
  function tgChip(d) {
    if (d === 'block_page') return 'pause';
    if (d === 'hold') return 'review';
    if (d === 'watch') return 'log';
    return 'run';
  }
  function tgRate(o) {
    if (!o || !isNum(o.rate)) return 'n/a';
    return pct(o.rate) + (isNum(o.k) && isNum(o.n) ? ' (' + o.k + ' of ' + o.n + ')' : '');
  }

  function renderTgAgents(tg) {
    var agents = tg.agents || [];
    var ul = $('tg-agents');
    var grid = $('tg-grid');
    clear(ul); clear(grid);
    var big = agents.length > 24;
    ul.hidden = big; grid.hidden = !big; $('tg-legend').hidden = !big;
    var hi = agents.filter(function (a) { return isNum(a.score) && a.score >= 0.7; });
    var restMax = agents.reduce(function (m, a) { return isNum(a.score) && a.score < 0.7 && a.score > m ? a.score : m; }, 0);
    $('tg-summary').textContent = agents.length + ' agents, each carrying its own risk score; ' + hi.length +
      ' at 0.70 or more; the highest of the rest scored ' + fmt(restMax, 2) + '.';
    var marks = [];
    if (big) {
      var types = [];
      agents.forEach(function (a) { if (types.indexOf(a.agent_type) < 0) types.push(a.agent_type); });
      types.forEach(function (t) {
        var row = el('div', 'arow');
        var list = agents.filter(function (a) { return a.agent_type === t; });
        row.appendChild(el('span', 'atype', t + ' (' + list.length + ')'));
        var tiles = el('div', 'tiles');
        list.forEach(function (a) {
          var s = a.score;
          var tile = el('span', 'tile ' + (!isNum(s) || s < 0.3 ? 'b0' : s < 0.7 ? 'b1' : 'b2'));
          tile.setAttribute('title', a.agent_id + ': ' + fmt(s, 2));
          marks.push([tile, a.hijacked]);
          tiles.appendChild(tile);
        });
        row.appendChild(tiles);
        grid.appendChild(row);
      });
    } else {
      agents.forEach(function (a) {
        var li = el('li', isNum(a.score) && a.score >= 0.7 ? 'hi' : '');
        li.appendChild(el('span', 'aid', a.agent_id));
        li.appendChild(el('span', 'asc', fmt(a.score, 2)));
        marks.push([li, a.hijacked]);
        ul.appendChild(li);
      });
    }
    var btn = $('tg-reveal');
    var note = $('tg-reveal-note');
    btn.addEventListener('click', function () {
      var open = btn.getAttribute('aria-expanded') !== 'true';
      btn.setAttribute('aria-expanded', String(open));
      btn.textContent = open ? 'Hide the answer' : 'Reveal which were hijacked';
      marks.forEach(function (m) {
        m[0].classList.toggle('hijacked', open && !!m[1]);
        var old = m[0].querySelector('.atag');
        if (old) m[0].removeChild(old);
        if (open && m[1] && !big) m[0].appendChild(el('span', 'atag', 'hijacked'));
      });
      var h = agents.filter(function (a) { return a.hijacked; });
      var hHi = h.filter(function (a) { return a.score >= 0.7; }).length;
      note.hidden = !open;
      note.textContent = open ? hHi + ' of the ' + hi.length + ' high-scoring agents were hijacked; ' + (h.length - hHi) + ' hijacked agents scored below 0.70.' : '';
    });
  }

  function tgCard(title, calls) {
    var c = el('div', 'card');
    c.appendChild(el('h3', null, title));
    var firstBlock = -1;
    calls.forEach(function (x, i) { if (firstBlock < 0 && x.decision === 'block_page') firstBlock = i; });
    calls.forEach(function (x, i) {
      var never = firstBlock >= 0 && i > firstBlock;
      var d = el('div', 'tg-call' + (i === firstBlock ? ' blocked' : '') + (never ? ' never' : ''));
      var top = el('div', 'top');
      top.appendChild(el('span', 'tool', 'Call ' + ((isNum(x.call_index) ? x.call_index : i) + 1) + ': ' + x.tool));
      var r = el('span');
      r.appendChild(el('span', 'score', 'score ' + fmt(x.score, 2) + ' '));
      if (never) r.appendChild(el('span', 'badge log', 'never ran'));
      else r.appendChild(el('span', 'badge ' + tgChip(x.decision), words(x.decision)));
      top.appendChild(r);
      d.appendChild(top);
      d.appendChild(el('div', 'meta', 'Agent ' + show(x.agent_id) + '. Destination: ' + (x.dest_host ? x.dest_host : 'none (internal tool)') +
        '. Classes: ' + show(x.classes) + '.'));
      var args = el('pre', 'args');
      args.textContent = JSON.stringify(x.masked_args || {}, null, 2);
      d.appendChild(args);
      if (never) d.appendChild(el('div', 'stop', 'Never ran: the session token was revoked when the call above was blocked.'));
      else if (x.reasons && x.reasons.length) d.appendChild(el('div', 'meta', 'Why: ' + x.reasons.join('; ')));
      if (i === firstBlock) d.appendChild(el('div', 'stop', 'Blocked before it ran, IT paged.'));
      c.appendChild(d);
    });
    return c;
  }

  function renderTgHero(tg) {
    var host = $('tg-hero');
    clear(host);
    var h = tg.hero || {};
    host.appendChild(tgCard('Legitimate: the vendor is approved for this data', h.hero_legit || []));
    host.appendChild(tgCard('Attack: the same data, headed to an unapproved host', h.hero_attack || []));
  }

  function renderTgCases(tg) {
    var tb = $('tg-cases');
    clear(tb);
    var cases = tg.cases || {};
    Object.keys(cases).forEach(function (k) {
      var c = cases[k];
      var tr = el('tr');
      tr.appendChild(el('td', null, TG_CASE_TITLES[k] || words(k)));
      tr.appendChild(el('td', null, TG_ACTOR[c.actor] || show(c.actor)));
      var td = el('td');
      td.appendChild(el('span', 'badge ' + tgChip(c.decision), words(c.decision)));
      tr.appendChild(td);
      tr.appendChild(el('td', null, usd(c.exposure_usd)));
      tb.appendChild(tr);
    });
  }

  function renderTgRows(tg) {
    var host = $('tg-rows');
    clear(host);
    var rows = tg.rows || {};
    var order = [['guard', 'Tool-call guard', 'g-N2'], ['http_layer_only', 'Network layer alone', 'g-base'], ['keyword_screen', 'Keyword screen', 'g-base']];
    var measures = [
      ['Blocked before the call ran', function (r) { return r.blocked_before_run; }, true, 'Higher is better. Share of attack sessions whose first harmful call never executed.'],
      ['False blocks', function (r) { return r.false_blocks; }, true, 'Lower is better. Share of normal sessions with any block or hold.'],
      ['IT pages per 1,000 sessions', function (r) { return { rate: r.pages_per_1000 }; }, false, 'Pages are deduplicated by destination host.']
    ];
    measures.forEach(function (m) {
      var c = el('div', 'card');
      c.appendChild(el('h3', null, m[0]));
      c.appendChild(el('p', 'small', m[3]));
      var ol = el('ol', 'bars mini');
      var vals = order.map(function (o) { return rows[o[0]] ? m[1](rows[o[0]]).rate : null; });
      var max = Math.max.apply(null, vals.map(function (v) { return isNum(v) ? v : 0; }).concat([0.0001]));
      order.forEach(function (o, i) {
        var r = rows[o[0]];
        if (!r) return;
        var v = vals[i];
        var li = el('li', o[2]);
        var top = el('div', 'row-top');
        top.appendChild(el('span', null, o[1]));
        top.appendChild(el('span', null, m[2] ? tgRate(m[1](r)) : fmt(v, 1)));
        var bt = el('div', 'bt');
        var bf = el('div', 'bf');
        bf.style.width = Math.round((m[2] ? (isNum(v) ? Math.min(1, v) : 0) : (isNum(v) ? v / max : 0)) * 100) + '%';
        bt.appendChild(bf);
        li.appendChild(top);
        li.appendChild(bt);
        ol.appendChild(li);
      });
      c.appendChild(ol);
      host.appendChild(c);
    });
    var g = rows.guard || {};
    var note = [];
    if (isNum(g.exposure_stopped_usd) || isNum(g.exposure_missed_usd)) note.push('The guard stopped ' + usd(g.exposure_stopped_usd) + ' of exposure before it left and missed ' + usd(g.exposure_missed_usd) + '.');
    if (g.legit_sensitive_blocked && isNum(g.legit_sensitive_blocked.k)) note.push('Legitimate sensitive calls blocked: ' + tgRate(g.legit_sensitive_blocked) + '.');
    note.push('The network layer alone never sees an outbound tool call: it only stops the bot campaign, by its inbound volume. The keyword screen only reads the customer message.');
    if (tg.sample) note.push('Illustrative sample, not measured.');
    else {
      var gr = (rows.guard || {}).blocked_before_run, hr = (rows.http_layer_only || {}).blocked_before_run, kr = (rows.keyword_screen || {}).blocked_before_run;
      var w = function (o) { return o && isNum(o.rate) ? Math.round(o.rate * 100) + '%' : 'n/a'; };
      note.push('The guard\'s ' + w(gr) + ' is true by construction: the simulated attacks are defined by the rule it applies, so read it as a wiring check. The baselines are the finding: the inbound network layer alone stops ' + w(hr) +
        ' of these attacks before the harmful call, and a keyword screen on the customer message ' + w(kr) + ', at hit rates we assumed.');
    }
    $('tg-note').textContent = note.join(' ');
    $('tg-meta').textContent = tg.sample ? '' : (tg.seeds || []).length + ' seeds, ' + tg.sessions_per_seed + ' sessions per seed, synthetic data. Rates are means over seeds.';
  }

  function initToolguard(tg) {
    $('sample-tg').hidden = !tg.sample;
    renderTgAgents(tg);
    renderTgHero(tg);
    renderTgCases(tg);
    renderTgRows(tg);
  }

  /* ================= boot ================= */
  loadFB('net-hero.json').then(function (d) { net = d; initNet(); })
    .catch(function () { loadError($('nplayer'), 'net-hero.json'); });
  loadFB('net-eval.json').then(function (d) { neval = d; initNeval(); })
    .catch(function () { loadError($('nbars'), 'net-eval.json'); });
  loadFB('toolguard.json').then(initToolguard)
    .catch(function () { loadError($('tg-agents'), 'toolguard.json'); });
  loadFB('fleet.json').then(initFleet)
    .catch(function () { loadError($('fleet-cats'), 'fleet.json'); });
})();
