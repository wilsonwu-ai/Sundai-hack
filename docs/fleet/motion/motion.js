// Fleet risk motion graphic. Every number on screen is read from window.FLEET (a fleet.json).
(function () {
  const F = window.FLEET;
  if (!F) { document.body.innerHTML = '<p style="font:40px sans-serif;padding:60px">data.js missing: run render.sh</p>'; return; }
  const $ = (s) => document.querySelector(s);
  const TOTAL = 15;

  // ---- build the DOM from the data ----
  const cats = F.categories || [];
  const standIn = new Set(F.stand_in_categories || []);
  const grid = $("#grid");
  const ROW0 = 188, STEP = 78;
  const riskyDots = [];
  cats.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "row"; row.style.top = (ROW0 + i * STEP) + "px";
    const lab = document.createElement("div"); lab.className = "lab"; lab.textContent = c.agent_type.charAt(0).toUpperCase() + c.agent_type.slice(1); row.appendChild(lab);
    if (standIn.has(c.agent_type) || c.source === "stand_in") {
      const tag = document.createElement("div"); tag.className = "tag"; tag.textContent = "stand-in"; row.appendChild(tag);
    }
    const ags = (F.agents || []).filter((a) => a.agent_type === c.agent_type);
    if (!ags.length) {
      const none = document.createElement("div"); none.className = "none"; none.textContent = "no logs yet"; row.appendChild(none);
    } else {
      const d = document.createElement("div"); d.className = "dots";
      ags.forEach((a) => {
        const dot = document.createElement("div"); dot.className = "dot"; d.appendChild(dot);
        if (a.risky) riskyDots.push({ el: dot, id: a.agent_id });
      });
      row.appendChild(d);
      const tally = document.createElement("div");
      tally.className = "tally" + (c.risky > 0 ? " r" : ""); tally.textContent = c.risky + " of " + c.agents;
      row.appendChild(tally);
    }
    grid.appendChild(row);
  });

  const nAgents = F.fleet.agents, nRisky = F.fleet.risky;
  $("#ccap").textContent = nRisky + " of " + nAgents + " agents are risky";
  $("#ccap2").textContent = "Paused far more often than a normal agent";

  // worst agent
  const W = F.worst || {};
  const wa = (F.agents || []).find((a) => a.agent_id === W.agent_id) || null;
  $("#wname").textContent = W.agent_id || "No risky agent";
  const wcat = W.agent_type || "";
  $("#wsub").textContent = wcat ? (wcat + " agent" + (standIn.has(wcat) ? ", stand-in data" : "")) : "";
  const wPaused = wa ? wa.paused : 0, wSess = wa ? wa.sessions : 0, wShare = wa ? wa.paused_share : 0;
  const budget = F.budget || 0.05;
  $("#wof").textContent = "of " + wSess;
  const axisMax = Math.max(wShare * 1.2, budget * 2);
  const BARW = 1760;
  const x5 = 80 + BARW * budget / axisMax - 3;
  $("#line5").style.left = x5 + "px";
  $("#line5lab").style.left = x5 + "px";
  const reasons = $("#reasons");
  (W.why || []).slice(0, 3).forEach((w, i) => {
    const r = document.createElement("div"); r.className = "reason";
    const n = document.createElement("div"); n.className = "n"; n.textContent = String(i + 1);
    const t = document.createElement("div"); t.className = "t"; t.textContent = w;
    r.appendChild(n); r.appendChild(t); reasons.appendChild(r);
  });

  // reveal
  const R = F.reveal || {};
  $("#cx").textContent = R.flagged_and_weaker;
  $("#cof").textContent = "of " + R.flagged;
  $("#cs1").textContent = "flagged agents really were the weaker ones";
  $("#cs2").textContent = "Caught " + R.flagged_and_weaker + " of " + R.weaker_total + " weaker agents";

  if (!F.sample) { $("#ill").style.display = "none"; $("#ill2").style.display = "none"; }

  // ---- timeline ----
  const tl = gsap.timeline({ paused: true });
  const COPPER = "#A8481B";

  tl.set("#grid", { autoAlpha: 0 }, 0);
  tl.set("#counter", { autoAlpha: 0 }, 0);
  tl.set("#worst", { autoAlpha: 0 }, 0);
  tl.set("#check", { autoAlpha: 0 }, 0);
  tl.set("#end", { autoAlpha: 0 }, 0);
  tl.set("#reasons", { autoAlpha: 0 }, 0);

  // 0-2 s: the question, big, then it settles into the header
  tl.fromTo("#q", { scale: 1.9, y: 380, opacity: 0 }, { scale: 1.9, y: 380, opacity: 1, duration: 0.5, ease: "power2.out" }, 0.1);
  tl.to("#q", { scale: 1, y: 0, duration: 0.8, ease: "power3.inOut" }, 1.3);

  // 2-5 s: the grid of dots
  tl.to("#grid", { autoAlpha: 1, duration: 0.3 }, 1.9);
  tl.from(grid.querySelectorAll(".row"), { x: -40, opacity: 0, duration: 0.4, stagger: 0.05, ease: "power3.out" }, 1.9);
  tl.from(grid.querySelectorAll(".dot"), { scale: 0, duration: 0.25, stagger: 0.012, ease: "back.out(2)", transformOrigin: "50% 50%" }, 2.1);
  tl.to("#counter", { autoAlpha: 1, duration: 0.4 }, 2.7);

  const T0 = 3.1, T1 = 4.6;
  riskyDots.forEach((d, i) => {
    const t = T0 + (T1 - T0) * (i + 0.5) / Math.max(1, riskyDots.length);
    tl.to(d.el, { backgroundColor: COPPER, duration: 0.15, ease: "power2.out" }, t);
    tl.fromTo(d.el, { scale: 1 }, { scale: 1.3, duration: 0.15, yoyo: true, repeat: 1, ease: "power2.out" }, t);
  });
  const prox = { p: 0 };
  const paint = () => { $("#cbig").textContent = Math.round(F.fleet.risky_share * 100 * prox.p) + "%"; };
  tl.to(prox, { p: 1, duration: T1 - T0, ease: "none" }, T0);
  tl.to("#cbig", { color: COPPER, duration: 0.4 }, T0);
  tl.to(grid.querySelectorAll(".tally"), { opacity: 1, duration: 0.4 }, T1);

  // 5-8 s: zoom to the worst agent; paused sessions tick up against the 5% line
  const wRow = Math.max(0, cats.findIndex((c) => c.agent_type === wcat));
  tl.set("#grid", { transformOrigin: "700px " + (ROW0 + wRow * STEP + 33) + "px" }, 4.9);
  tl.to("#grid", { scale: 1.5, autoAlpha: 0, duration: 0.6, ease: "power3.in" }, 4.95);
  tl.to("#counter", { autoAlpha: 0, duration: 0.4 }, 4.95);
  tl.to("#q", { opacity: 0, duration: 0.3 }, 4.95);
  tl.fromTo("#worst", { autoAlpha: 0, scale: 0.92 }, { autoAlpha: 1, scale: 1, duration: 0.55, ease: "power3.out", transformOrigin: "50% 50%" }, 5.45);
  tl.fromTo(["#line5", "#line5lab"], { opacity: 0 }, { opacity: 1, duration: 0.4 }, 5.7);
  const tick = { p: 0 };
  const paintW = () => {
    $("#wp").textContent = Math.round(wPaused * tick.p);
    $("#wfill").style.width = (BARW * (wShare * tick.p) / axisMax) + "px";
  };
  tl.to(tick, { p: 1, duration: 1.7, ease: "steps(" + Math.max(1, wPaused) + ")" }, 6.0);

  // 8-11 s: the three reasons, one by one
  tl.to(["#wmetric", "#bar", "#line5", "#line5lab"], { autoAlpha: 0, duration: 0.35 }, 7.95);
  tl.set("#reasons", { autoAlpha: 1 }, 8.1);
  reasons.querySelectorAll(".reason").forEach((r, i) => {
    tl.fromTo(r, { opacity: 0, x: -50 }, { opacity: 1, x: 0, duration: 0.45, ease: "power3.out" }, 8.3 + i * 0.8);
  });

  // 11-13 s: checked against the sealed answers
  tl.to("#worst", { autoAlpha: 0, duration: 0.35 }, 10.95);
  tl.to("#check", { autoAlpha: 1, duration: 0.4 }, 11.3);
  tl.from("#check .k", { y: 20, opacity: 0, duration: 0.5, ease: "power3.out" }, 11.3);
  tl.from("#check .x", { y: 30, opacity: 0, duration: 0.6, ease: "power3.out" }, 11.55);
  tl.from(["#check .s1", "#check .s2"], { opacity: 0, y: 14, duration: 0.5, stagger: 0.2, ease: "power2.out" }, 12.0);

  // 13-15 s: close
  tl.to("#end", { autoAlpha: 1, duration: 0.5, ease: "power2.inOut" }, 12.95);
  tl.from("#end .brandbar", { scaleX: 0, duration: 0.5, ease: "power3.out" }, 13.1);
  tl.from("#end h1", { y: 24, opacity: 0, duration: 0.7, ease: "power3.out" }, 13.2);
  tl.to("#ill", { opacity: 0, duration: 0.2 }, 12.95);
  tl.to("#ill2", { opacity: 1, duration: 0.2 }, 13.0);
  tl.to({}, { duration: 0.01 }, TOTAL - 0.01);

  window.__duration = TOTAL;
  window.__seek = (t) => { tl.seek(t, false); paint(); paintW(); return true; };
  window.__seek(0);
})();
