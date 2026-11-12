/* Torch Bench window. Plain JavaScript, no libraries. All text reaches the page through
   textContent (never innerHTML), so nothing Torch prints can run as code. */
(function () {
  "use strict";

  var TOKEN = document.querySelector('meta[name="torchbench-token"]').content;
  var main = document.getElementById("main");
  var S = {
    state: null, view: "jobs", dash: null, dashSig: "", dashAt: 0, open: {}, details: {}, disk: null,
    wizard: 0, form: {}, cmds: [], cmdLast: 0, showRoutine: false, timers: {}, signin: null,
    arg: "", fileGen: 0, term: null, termGen: 0, jobLogs: {}, projGen: 0, projEdit: false, projNote: ""
  };

  // ---- small helpers -------------------------------------------------------------------------
  function h(tag, attrs) {
    var el = document.createElement(tag), i, k;
    attrs = attrs || {};
    for (k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k) || attrs[k] == null || attrs[k] === false) continue;
      if (k === "class") el.className = attrs[k];
      else if (k === "text") el.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), attrs[k]);
      else if (k === "style") el.style.cssText = attrs[k];
      else el.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    }
    for (i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(el, k); }); return; }
    el.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
  }
  function show() { main.replaceChildren.apply(main, Array.prototype.filter.call(arguments, function (x) { return x != null && x !== false; })); }
  function api(method, path, body) {
    var opts = { method: method, headers: { "X-Torchbench-Token": TOKEN } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || "Torch Bench did not answer (" + r.status + ").");
        return data;
      });
    }, function () { throw new Error("Torch Bench has stopped. Start it again with the torchbench command."); });
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function dur(seconds) {
    if (seconds == null) return "";
    if (seconds < 90) return Math.round(seconds) + " s";
    if (seconds < 5400) return Math.round(seconds / 60) + " min";
    if (seconds < 129600) { var hrs = seconds / 3600; return (hrs >= 10 ? Math.round(hrs) : Math.round(hrs * 10) / 10) + " h"; }
    var d = seconds / 86400; return (d >= 10 ? Math.round(d) : Math.round(d * 10) / 10) + " days";
  }
  function bytes(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    var units = ["KB", "MB", "GB", "TB"], v = n / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (v >= 100 ? Math.round(v) : Math.round(v * 10) / 10) + " " + units[i];
  }
  function decimalSize(n) {            // quotas are quoted in decimal units (5 TB), so match them
    if (n == null) return "";
    var units = ["B", "KB", "MB", "GB", "TB", "PB"], i = 0;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
    return (n >= 100 || i === 0 ? Math.round(n) : Math.round(n * 10) / 10) + " " + units[i];
  }
  function tidyLimit(text) { return (text || "").replace(/\.0(?=[A-Za-z])/, "").replace(/(\d)([A-Za-z])/, "$1 $2"); }
  function ago(seconds) { return seconds == null ? "" : seconds < 5 ? "just now" : dur(seconds) + " ago"; }
  function stop(name) { if (S.timers[name]) { clearTimeout(S.timers[name]); S.timers[name] = null; } }
  function later(name, ms, fn) { stop(name); S.timers[name] = setTimeout(fn, ms); }
  function copy(text, button) {
    var done = function () { if (button) { var t = button.textContent; button.textContent = "Copied"; setTimeout(function () { button.textContent = t; }, 1500); } };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
  }

  // ---- top bar -------------------------------------------------------------------------------
  function drawTop() {
    var st = S.state, tabs = document.getElementById("tabs"), conn = document.getElementById("conn");
    document.getElementById("demo-note").hidden = !(st && st.demo);
    var ready = st && st.configured && S.view !== "setup";
    tabs.hidden = !ready; conn.hidden = !ready;
    if (!ready) return;
    Array.prototype.forEach.call(tabs.querySelectorAll("a"), function (a) {
      var here = S.view === "file" ? "files" : S.view === "commands" ? "help" : S.view;
      if (a.getAttribute("data-view") === here) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    var connected = S.dash ? S.dash.connected : st.connected;
    conn.replaceChildren(
      h("span", { class: "conn-text" },
        h("span", { class: "lamp " + (connected ? "on" : connected === false ? "off" : "") }),
        connected ? h("span", {}, "Connected", h("span", { class: "who" }, " as " + st.config.netid))
                  : h("span", {}, connected === false ? "Not connected to Torch" : "Checking the connection")),
      connected ? h("button", { class: "quiet small", onclick: disconnect, title: "Close the shared Torch login on this Mac" }, "Disconnect")
                : connected === false ? h("button", { class: "small", onclick: openSignin }, "Sign in") : null);
  }
  function disconnect() {
    api("POST", "/api/disconnect").then(function () { S.state.connected = false; if (S.dash) S.dash.connected = false; S.dashSig = ""; route(); });
  }

  // ---- sign-in panel (used by setup and by the Sign in button) -------------------------------
  function signinPanel(onConnected, onClose) {
    var box = h("div", { class: "signin", role: "region", "aria-live": "polite", "aria-label": "Sign in to Torch" });
    var sig = "", rawEl = null, alive = true;

    function act(path, body) { return api("POST", "/api/login/" + path, body).then(draw, fail); }
    function fail(err) { box.replaceChildren(h("h2", {}, "The sign-in could not start"), h("p", { class: "error-text" }, err.message), h("div", { class: "row" }, h("button", { onclick: start }, "Try again"), onClose ? h("button", { class: "quiet", onclick: close }, "Close") : null)); }
    function start() { sig = ""; box.replaceChildren(h("p", {}, h("span", { class: "pulse" }), "Starting the sign-in")); api("POST", "/api/login/start").then(function (st) { draw(st); poll(); }, fail); }
    function close() { alive = false; stop("login"); if (onClose) onClose(); }
    function poll() { later("login", 1000, function () { if (!alive) return; api("GET", "/api/login").then(function (st) { draw(st); if (["connected", "failed", "vpn", "cancelled", "idle"].indexOf(st.step) < 0) poll(); }, function () { poll(); }); }); }

    function rawBlock(st, openIt) {
      rawEl = h("pre", { class: "raw" }, st.raw || "(nothing yet)");
      return h("details", { class: "more", open: openIt }, h("summary", {}, "What Torch printed"), h("div", {}, rawEl));
    }
    function replyForm(label, masked, hint) {
      var input = h("input", { type: masked ? "password" : "text", id: "tb-reply", autocomplete: "off", spellcheck: "false" });
      var form = h("form", { onsubmit: function (e) { e.preventDefault(); var v = input.value; input.value = ""; act("input", { text: v }).then(poll); } },
        h("div", { class: "field" }, h("label", { for: "tb-reply" }, label, hint ? h("span", { class: "hint" }, " " + hint) : null), input),
        h("div", { class: "row" }, h("button", { type: "submit" }, "Send to Torch"), h("button", { type: "button", class: "quiet", onclick: function () { act("cancel"); } }, "Cancel sign-in")));
      setTimeout(function () { input.focus(); }, 0);
      return form;
    }
    function cancelRow() { return h("div", { class: "row" }, h("button", { class: "quiet", onclick: function () { act("cancel"); } }, "Cancel sign-in")); }

    function draw(st) {
      if (!alive) return;
      var now = [st.step, st.code, st.message, st.needs_enter, st.fingerprint].join("|");
      if (now === sig) { if (rawEl) rawEl.textContent = st.raw || "(nothing yet)"; return; }
      sig = now; rawEl = null;
      var kids;
      if (st.step === "connected") {
        kids = [h("h2", {}, "You are signed in to Torch"), h("p", {}, "This sign-in is shared by everything Torch Bench does until it expires or you disconnect.")];
        box.replaceChildren.apply(box, kids);
        stop("login");
        setTimeout(function () { if (alive && onConnected) onConnected(); }, 700);
        return;
      }
      if (st.step === "vpn") {
        var vpn = (S.state.config.help_links || []).filter(function (l) { return /vpn/i.test(l.title); })[0];
        kids = [h("h2", {}, "Connect to the NYU VPN first"),
          h("p", {}, "This Mac cannot reach " + st.host + ". Off campus, Torch only answers through the NYU VPN. Open Cisco Secure Client, connect to vpn.nyu.edu, then try again."),
          vpn ? h("p", {}, h("a", { href: vpn.url, target: "_blank", rel: "noopener" }, "How to install the NYU VPN on a Mac")) : null,
          h("div", { class: "row" }, h("button", { onclick: start }, "Try again"), onClose ? h("button", { class: "quiet", onclick: close }, "Close") : null),
          st.raw ? rawBlock(st, false) : null];
      } else if (st.step === "device") {
        kids = [h("h2", {}, "Enter this code on Microsoft's sign-in page"),
          h("div", {}, h("span", { class: "code", "aria-label": "One-time code" }, st.code)),
          h("p", { class: "hint" }, (st.copied ? "The code is already copied. " : "") + (st.browser_opened ? "The sign-in page opened in a new tab." : "")),
          h("ol", {},
            h("li", {}, st.browser_opened ? "In the new tab, paste the code." : ["Open ", st.url_trusted ? h("a", { href: st.url, target: "_blank", rel: "noopener" }, st.url) : h("b", {}, st.url), " and enter the code."]),
            h("li", {}, "Sign in with your NYU account (NetID@nyu.edu) and approve the Duo prompt on your phone."),
            st.needs_enter ? h("li", {}, "Come back here and choose the button below.") : h("li", {}, "Come back here. Torch Bench notices by itself when you are done.")),
          !st.url_trusted ? h("p", { class: "error-text" }, "Torch printed a sign-in address Torch Bench does not recognise, so it was not opened for you. Check it before you use it.") : null,
          h("div", { class: "row" },
            st.needs_enter ? h("button", { onclick: function () { act("continue").then(poll); } }, "I have signed in") : h("span", {}, h("span", { class: "pulse" }), "Waiting for you to finish in the browser"),
            h("button", { class: "quiet", onclick: function (e) { copy(st.code, e.target); } }, "Copy the code"),
            st.url_trusted ? h("a", { class: "button quiet", href: st.url, target: "_blank", rel: "noopener" }, "Open the page again") : null,
            h("button", { class: "quiet", onclick: function () { act("cancel"); } }, "Cancel")),
          rawBlock(st, false)];
      } else if (st.step === "duo") {
        kids = [h("h2", {}, "Approve the Duo prompt on your phone"), h("p", {}, h("span", { class: "pulse" }), "Torch is waiting for Duo."),
          replyForm("If Torch is asking you to choose an option, type it here", false, "(for example 1 for a push)"), rawBlock(st, true)];
      } else if (st.step === "hostkey") {
        kids = [h("h2", {}, "First connection to this Torch machine"),
          h("p", {}, "Your Mac has not connected to " + st.host + " before. If you are on the NYU network or VPN, this is expected."),
          st.fingerprint ? h("code", { class: "fingerprint" }, st.fingerprint) : null,
          h("div", { class: "row" }, h("button", { onclick: function () { act("trust-host").then(poll); } }, "Trust this machine and continue"), h("button", { class: "quiet", onclick: function () { act("cancel"); } }, "Cancel")),
          rawBlock(st, true)];
      } else if (st.step === "password") {
        kids = [h("h2", {}, "Torch is asking for a password"),
          h("p", {}, "What you type goes straight to the sign-in and is not saved anywhere."),
          replyForm("Password", true), rawBlock(st, true)];
      } else if (st.step === "prompt") {
        kids = [h("h2", {}, "Torch is asking something"),
          h("p", {}, "Torch Bench does not recognise this step, so here is exactly what Torch printed. Type your answer below; it goes straight to the sign-in and is not saved."),
          rawBlock(st, true), replyForm("Your answer", true)];
      } else if (st.step === "failed" || st.step === "cancelled") {
        kids = [h("h2", {}, st.step === "cancelled" ? "Sign-in cancelled" : "The sign-in did not finish"),
          h("p", {}, st.message),
          h("div", { class: "row" }, h("button", { onclick: start }, "Try again"),
            st.raw ? h("button", { class: "quiet", onclick: function (e) { api("POST", "/api/login/transcript").then(function (r) { e.target.textContent = "Saved to " + r.saved; e.target.disabled = true; }); }, title: "Codes and anything you typed are blanked out" }, "Save what Torch printed") : null,
            onClose ? h("button", { class: "quiet", onclick: close }, "Close") : null),
          st.raw ? rawBlock(st, st.step === "failed") : null];
      } else {
        kids = [h("h2", {}, "Signing in to Torch"), h("p", {}, h("span", { class: "pulse" }), (st.message || "Contacting Torch")), cancelRow(), rawBlock(st, false)];
      }
      box.replaceChildren.apply(box, kids.filter(Boolean));
    }

    return { el: box, start: start, close: close };
  }

  function openSignin() {
    if (S.signin) return;
    var overlay = h("div", { class: "overlay" });
    var panel = signinPanel(function () { shut(); S.state.connected = true; S.dashSig = ""; loadDash(true); }, shut);
    function shut() {
      if (!S.signin) return;            // close() calls back into shut(); do the work once
      S.signin = null; panel.close(); overlay.remove();
      api("GET", "/api/state").then(function (st) { S.state = st; drawTop(); });
    }
    S.signin = panel;
    overlay.appendChild(panel.el);
    overlay.addEventListener("keydown", function (e) { if (e.key === "Escape") shut(); });
    document.body.appendChild(overlay);
    panel.start();
  }

  // ---- first-run setup -----------------------------------------------------------------------
  var STEP_NAMES = ["Before you start", "About you", "Connection settings", "Sign in"];
  function drawSetup() {
    stop("dash"); stop("cmds");
    var st = S.state, cfg = st.config, n = S.wizard;
    var steps = h("ol", { class: "steps" }, STEP_NAMES.map(function (name, i) { return h("li", { class: i < n ? "done" : i === n ? "now" : "", "aria-current": i === n ? "step" : null }, name); }));
    var panel = h("div", { class: "panel" });
    var err = h("p", { class: "error-text", role: "alert" });
    function go(i) { S.wizard = i; drawSetup(); main.focus(); }

    if (n === 0) {
      add(panel, [h("h1", {}, "Set up Torch Bench"), h("p", {}, st.about),
        h("p", {}, "You need four things before signing in. Each link opens NYU's own page for it."),
        h("ul", { class: "links" }, (cfg.help_links || []).slice(0, 5).map(function (l) {
          return h("li", {}, h("a", { href: l.url, target: "_blank", rel: "noopener" }, l.title), h("p", {}, l.note)); })),
        h("button", { onclick: function () { go(1); } }, "I have these, continue")]);
    } else if (n === 1) {
      var f = S.form;
      if (f.netid == null) { f.netid = cfg.netid || ""; f.mode = cfg.mode || "colleague"; f.project_name = cfg.project_name || ""; f.remote_dir = cfg.remote_dir || ""; f.sbatch_account = cfg.sbatch_account || ""; f.local_dir = cfg.local_dir || ""; f.extra = {}; }
      var inp = function (id, key, ph) { return h("input", { type: "text", id: id, value: f[key], placeholder: ph || null, autocomplete: "off", spellcheck: "false", oninput: function (e) { f[key] = e.target.value; } }); };
      var radio = function (value, title, text) { return h("label", { class: "choice" }, h("input", { type: "radio", name: "mode", value: value, checked: f.mode === value, onchange: function () { f.mode = value; } }), h("span", {}, h("b", {}, title), text)); };
      var imported = h("p", { class: "hint" }, f.importedName ? "Loaded " + f.importedName + "." : "");
      var file = h("input", { type: "file", accept: ".json,application/json", id: "tb-file", onchange: function (e) {
        var fl = e.target.files[0]; if (!fl) return;
        fl.text().then(function (t) {
          try {
            var p = JSON.parse(t); if (!p.torchbench_project_file) throw new Error();
            ["project_name", "remote_dir", "sbatch_account"].forEach(function (k) { if (typeof p[k] === "string") f[k] = p[k]; });
            f.extra = {}; ["default_partition", "gpu_partitions", "cpu_partitions", "launchers", "help_links", "remote_host", "remote_dir_template"].forEach(function (k) { if (p[k] != null) f.extra[k] = p[k]; });
            f.importedName = fl.name; drawSetup();
          } catch (x) { imported.textContent = "That file is not a Torch Bench project file."; }
        }); } });
      add(panel, [h("h1", {}, "About you"),
        h("div", { class: "field" }, h("label", { for: "tb-netid" }, "Your NYU NetID ", h("span", { class: "hint" }, "for example abc1234")), inp("tb-netid", "netid")),
        h("div", { class: "field", role: "radiogroup", "aria-label": "How you will use Torch Bench" },
          radio("colleague", "I want to follow the work", "Watch jobs, read files and logs, copy results to this Mac. Nothing here changes the code on Torch."),
          radio("owner", "I run a project", "Everything above, plus pushing your code to Torch. Later versions add submitting and cancelling jobs.")),
        h("div", { class: "field" }, h("label", { for: "tb-file" }, "Project file ", h("span", { class: "hint" }, "optional. The person who runs the project can give you one; it fills in the rest.")), file, imported),
        err,
        h("div", { class: "row" }, h("button", { onclick: function () {
          var body = { netid: f.netid, mode: f.mode };
          if (f.importedName) { body.project_name = f.project_name; body.remote_dir = f.remote_dir; body.sbatch_account = f.sbatch_account; }
          Object.keys(f.extra || {}).forEach(function (k) { body[k] = f.extra[k]; });
          api("POST", "/api/setup/save", body).then(function (s2) { S.state = s2; go(2); }, function (e) { err.textContent = e.message; });
        } }, "Save and continue"), h("button", { class: "quiet", onclick: function () { go(0); } }, "Back"))]);
    } else if (n === 2) {
      add(panel, [h("h1", {}, "Connection settings"),
        h("p", {}, "Torch asks for a Microsoft sign-in and Duo every time something connects. With the entry below in your Mac's SSH settings, you sign in once and everything Torch Bench does afterwards shares that one sign-in. No password or code is stored.")]);
      var body = h("div", {}, h("p", { class: "hint" }, "Checking your SSH settings"));
      panel.appendChild(body);
      var paint = function (ss) {
        if (ss.ok) {
          S.state.ssh_ready = true;
          body.replaceChildren(h("p", { class: "ok-line" }, ss.has_block ? "The entry is in place." : "Your SSH settings already have a working entry for Torch. Torch Bench will use it as it is."),
            h("div", { class: "row" }, h("button", { onclick: function () { go(3); } }, "Continue to sign in")));
        } else if (ss.own_entry) {
          body.replaceChildren(h("p", {}, "Your SSH settings (" + ss.file + ") already have their own entry called “" + cfg.ssh_alias + "”, so Torch Bench will not change it. Add the ControlMaster, ControlPath and ControlPersist lines below to that entry, then check again."),
            h("pre", { class: "block" }, ss.block), h("ul", {}, ss.problems.map(function (p) { return h("li", {}, p); })),
            h("div", { class: "row" }, h("button", { onclick: check }, "Check again")));
        } else {
          body.replaceChildren(h("p", {}, "This will be added to " + ss.file + ". Anything already in that file is kept, and a backup copy is made first."),
            h("pre", { class: "block" }, ss.block), err,
            h("div", { class: "row" }, h("button", { onclick: function () { api("POST", "/api/setup/ssh").then(paint, function (e) { err.textContent = e.message; }); } }, "Add this to my SSH settings"),
              h("button", { class: "quiet", onclick: function () { go(1); } }, "Back")));
        }
      };
      var check = function () { api("GET", "/api/setup/ssh").then(paint, function (e) { body.replaceChildren(h("p", { class: "error-text" }, e.message)); }); };
      check();
    } else {
      var sp = signinPanel(function () { api("GET", "/api/state").then(function (s2) { S.state = s2; S.view = "jobs"; if (location.hash !== "#jobs") location.hash = "#jobs"; route(); }); });
      add(panel, [h("h1", {}, "Sign in to Torch"),
        h("p", {}, "Torch Bench starts the connection and shows you each step. You do the signing in yourself, on Microsoft's page and on your phone."),
        sp.el]);
      sp.start();
    }
    show(h("div", { class: "setup" }, h("nav", { "aria-label": "Setup steps" }, steps), panel));
    drawTop();
  }

  // ---- jobs page -----------------------------------------------------------------------------
  function loadDash(force) {
    stop("dash");
    if (S.view !== "jobs" || !S.state || !S.state.configured) return;
    if (document.hidden) { later("dash", 5000, loadDash); return; }   // nothing is asked of Torch while the window is hidden
    api("GET", "/api/dashboard" + (force ? "?force=1" : "")).then(function (d) {
      S.dash = d; S.dashAt = Date.now();
      drawJobs();
      var settling = d.connected !== false && (d.refreshing || !d.sources.queue.loaded);
      later("dash", settling ? 1500 : 30000, loadDash);
    }, function (e) {
      show(h("div", { class: "notice stop" }, h("div", {}, h("strong", {}, "Torch Bench has a problem"), h("p", {}, e.message))));
      later("dash", 10000, loadDash);
    });
  }

  function taskClass(kind) { return kind === "running" ? "t-run" : kind === "waiting" ? "t-wait" : kind === "failed" ? "t-fail" : ""; }
  function countsText(c) {
    var parts = [];
    if (c.running) parts.push(c.running + " running");
    if (c.waiting) parts.push(c.waiting + " waiting");
    if (c.done) parts.push(c.done + " finished");
    if (c.failed) parts.push(c.failed + " failed");
    if (c.cancelled) parts.push(c.cancelled + " cancelled");
    if (c.other) parts.push(c.other + " other");
    return parts.join(", ");
  }

  function jobRow(g, list) {
    var key = list + ":" + g.id, open = !!S.open[key];
    var first = g.tasks[0] || {};
    var chip = { running: "Running", waiting: "Waiting", failed: "Failed", done: "Finished", cancelled: "Cancelled", other: "Other" }[g.kind] || g.kind;
    var meta = "Job " + g.id + (g.partition ? " on " + g.partition : "");
    var side;
    if (list === "q") {
      if (g.is_array || g.total > 1) {
        var r = g.counts.running || 0, w = g.counts.waiting || 0;
        side = [h("div", { class: "bar", role: "img", "aria-label": r + " running, " + w + " waiting" }, h("i", { style: "width:" + (100 * r / g.total) + "%" }), h("i", { class: "w", style: "width:" + (100 * w / g.total) + "%" })),
          countsText(g.counts)];
      } else if (g.kind === "running" && first.limit_s) {
        side = [h("div", { class: "bar", role: "img", "aria-label": "time used" }, h("i", { style: "width:" + Math.min(100, 100 * (first.elapsed_s || 0) / first.limit_s) + "%" })),
          dur(first.elapsed_s) + " of " + dur(first.limit_s)];
      } else if (g.waited) { side = ["waiting " + g.waited]; }
    } else {
      side = [g.ended || ""];
      if (g.total > 1) meta += ", " + countsText(g.counts);
    }
    var head = h("button", { class: "job-head", "aria-expanded": open ? "true" : "false", onclick: function () { S.open[key] = !open; drawJobs(true); } },
      h("span", { class: "state " + g.kind }, chip),
      h("span", {}, h("div", { class: "job-name" }, g.name || "(no name)"), h("div", { class: "job-meta" }, meta),
        g.why ? h("div", { class: "why" + (g.why.stuck ? " stuck" : "") }, g.why.title) : null),
      h("span", { class: "job-side" }, side),
      h("span", { class: "chev", "aria-hidden": "true" }, "›"));
    var li = h("li", { class: "job" + (list === "h" ? " compact" : "") }, head);
    if (open) { li.setAttribute("open-row", ""); li.appendChild(jobBody(g, list)); }
    return li;
  }

  function jobBody(g, list) {
    var body = h("div", { class: "job-body" });
    if (g.why) {
      add(body, h("p", { class: "why-detail" }, g.why.detail + (g.scheduler_estimate ? " " + g.scheduler_estimate + " Estimates move as other jobs come and go." : "")));
    }
    var facts = [];
    if (g.gpus) facts.push(plural(g.gpus, "GPU", "GPUs") + (g.is_array ? " per task" : ""));
    if (g.array_limit) facts.push("at most " + g.array_limit + " tasks at once");
    if (list === "h" && g.longest) facts.push((g.total > 1 ? "longest task ran " : "ran ") + g.longest);
    if (facts.length) add(body, h("p", { class: "hint" }, facts.join(", ") + "."));
    var rows = g.tasks.map(function (t) {
      return h("tr", {}, h("td", {}, t.id), h("td", { class: taskClass(t.kind) }, t.state_word + (t.note ? " " + t.note : "")),
        list === "q" ? [h("td", {}, t.kind === "running" ? t.elapsed : ""), h("td", {}, t.limit), h("td", {}, t.node || (t.task_count > 1 ? t.task_count + " tasks" : ""))]
                     : [h("td", {}, t.elapsed), h("td", {}, t.ended), h("td", {}, t.exit_code && t.exit_code !== "0:0" ? "exit " + t.exit_code : "")]);
    });
    var heads = list === "q" ? ["Job", "State", "Time used", "Time allowed", "Machine"] : ["Job", "Result", "Ran for", "Ended", ""];
    if (g.tasks.length > 1 || list === "h") {
      add(body, h("div", { class: "tasks-scroll" }, h("table", { class: "tasks" }, h("thead", {}, h("tr", {}, heads.map(function (x) { return h("th", { scope: "col" }, x); }))), h("tbody", {}, rows))));
      if (g.hidden_tasks) add(body, h("p", { class: "hint" }, "and " + g.hidden_tasks + " more"));
    }
    var id = (g.tasks[0] && /^\d+(_\d+)?$/.test(g.tasks[0].id)) ? g.tasks[0].id : g.id;
    var det = S.details[id], logs = S.jobLogs[id];
    var actions = h("div", { class: "row actions" });
    if (!logs) add(actions, h("button", { class: "quiet small", onclick: function (e) {
      e.target.disabled = true; e.target.textContent = "Looking";
      api("GET", "/api/job/logs?id=" + encodeURIComponent(id)).then(function (r) { S.jobLogs[id] = r; drawJobs(true); }, function (x) { S.jobLogs[id] = { ok: false, error: x.message }; drawJobs(true); });
    } }, "Log files"));
    if (!det) add(actions, h("button", { class: "quiet small", onclick: function (e) {
      e.target.disabled = true; e.target.textContent = "Asking Torch";
      api("GET", "/api/job?id=" + encodeURIComponent(id)).then(function (d) { S.details[id] = d; drawJobs(true); }, function (x) { S.details[id] = { ok: false, error: x.message }; drawJobs(true); });
    } }, "Scheduler details"));
    if (actions.childNodes.length) add(body, actions);
    if (logs) {
      if (!logs.ok) add(body, h("p", { class: "hint" }, logs.error));
      else if (!logs.files.length) add(body, h("p", { class: "hint" }, "No file in the project's logs folder has " + id.split("_")[0] + " in its name."));
      else {
        add(body, h("ul", { class: "loglist" }, logs.files.slice(0, 40).map(function (f) {
          return h("li", {}, h("a", { href: "#file/" + f.place + "/" + encodeURIComponent(f.rel) }, f.name), h("span", { class: "hint" }, "  " + bytes(f.size) + ", changed " + f.modified)); })));
        if (logs.files.length > 40) add(body, h("p", { class: "hint" }, "and " + (logs.files.length - 40) + " more, in the project's logs folder under Files"));
      }
    }
    if (det) {
      if (!det.ok) add(body, h("p", { class: "hint" }, det.error));
      else {
        if (g.priority) {
          var p = g.priority;
          add(body, h("p", { class: "hint" }, "Priority " + Math.round(p.priority) + ": fair-share " + Math.round(p.fairshare) + ", time waiting " + Math.round(p.age) + ", partition " + Math.round(p.partition) + ", job size " + Math.round(p.jobsize) + ". Higher starts sooner."));
        }
        add(body, h("dl", { class: "kv" }, det.fields.map(function (f) { return [h("dt", {}, f.key), h("dd", {}, f.value)]; })));
      }
    }
    return body;
  }

  function headline(s) {
    var run = s.running, wait = s.waiting, el = h("h1", { class: "headline" });
    if (!run && !wait) { el.textContent = "Nothing is running or waiting."; return el; }
    if (run) add(el, [h("span", { class: "n-run" }, String(run)), run === 1 ? " job is running" : " jobs are running"]);
    if (run && wait) add(el, [" and ", h("span", { class: "n-wait" }, String(wait)), wait === 1 ? " is waiting." : " are waiting."]);
    else if (wait) add(el, [h("span", { class: "n-wait" }, String(wait)), wait === 1 ? " job is waiting to start." : " jobs are waiting to start."]);
    else add(el, ".");
    return el;
  }

  function sideColumn(d) {
    var f = d.fairshare, q = d.quota, kids = [];
    kids.push(h("section", {}, h("h2", {}, "Your place in line"),
      f.value == null ? h("p", {}, d.sources.fairshare.error || "Not loaded yet.")
        : [h("div", { class: "big" }, f.value.toFixed(2), " ", h("small", {}, f.title.toLowerCase())),
           h("div", { class: "meter", role: "img", "aria-label": "fair-share " + f.value.toFixed(2) + " on a scale of 0 to 1" }, h("i", { style: "width:" + Math.max(2, 100 * f.value) + "%" })),
           h("details", { class: "explain" }, h("summary", {}, "What this means"), h("p", {}, "Fair-share runs from 0 to 1; higher means your jobs start sooner. " + f.detail))]));

    var storage = h("section", {}, h("h2", {}, "Storage"));
    if (q.parsed) {
      q.filesystems.forEach(function (fs) {
        var warn = fs.worst_pct >= q.warn_percent;
        add(storage, h("div", { class: "fs" },
          h("div", { class: "fs-row" }, h("b", {}, fs.fs), h("span", {}, decimalSize(fs.space_used_bytes) + (fs.space_limit ? " of " + tidyLimit(fs.space_limit) : ""))),
          h("div", { class: "meter" + (warn ? " warn" : ""), role: "img", "aria-label": fs.fs + " " + Math.round(fs.space_pct) + " percent full" }, h("i", { style: "width:" + Math.min(100, fs.space_pct) + "%" })),
          fs.files_pct != null && fs.files_pct >= 70 ? h("div", { class: "fs-row hint" }, h("span", {}, "number of files"), h("span", {}, Math.round(fs.files_pct) + "% of the limit")) : null));
      });
      add(storage, h("p", {}, "Torch's quota report can lag by hours."));
    } else if (q.raw) {
      add(storage, [h("p", {}, "The quota report could not be read, so here it is as Torch printed it."), h("pre", { class: "raw" }, q.raw)]);
    } else {
      add(storage, h("p", {}, d.sources.quota.error || "Not loaded yet."));
    }
    kids.push(storage);

    kids.push(h("section", {}, h("h2", {}, "How busy Torch is"),
      h("table", { class: "parts" }, h("tbody", {}, d.partitions.map(function (p) {
        var free = p.readable && p.role === "GPU" ? p.gpus_free + " of " + p.gpus_total + " GPUs free" : p.readable ? p.nodes_usable + " of " + p.nodes + " machines open" : "";
        return h("tr", {}, h("td", {}, h("div", { class: "pname" }, p.partition), p.typical_wait ? h("div", { class: "hint" }, "recent wait about " + p.typical_wait) : null), h("td", {}, free));
      }))),
      d.partitions.some(function (p) { return p.typical_wait; }) ? h("p", {}, "Waits are estimates from your own jobs this week.") : null));
    return h("aside", { class: "side", "aria-label": "Standing, storage and cluster load" }, kids);
  }

  function drawJobs(forceDraw) {
    var d = S.dash; if (!d || S.view !== "jobs") return;
    var sig = JSON.stringify([d.connected, d.summary, d.queue, d.history, d.fairshare, d.quota, d.partitions, d.sources.queue.error, d.sources.history.error]);
    drawTop();
    var fresh = document.getElementById("tb-fresh");
    if (!forceDraw && sig === S.dashSig && fresh) { fresh.textContent = freshText(d); return; }
    S.dashSig = sig;
    var keepScroll = document.querySelector(".finished-scroll"), scrollTop = keepScroll ? keepScroll.scrollTop : 0;
    var top = [];
    if (d.connected === false) {
      top.push(h("div", { class: "notice flame", role: "status" }, h("div", {}, h("strong", {}, "Not connected to Torch"),
        h("p", {}, d.sources.queue.loaded ? "This is the last answer Torch gave, " + ago(d.sources.queue.age_s) + "." : "Signing in takes about a minute and needs your phone for Duo.")),
        h("button", { onclick: openSignin }, "Sign in to Torch")));
    }
    if (d.quota.warnings.length) {
      top.push(h("div", { class: "notice stop", role: "alert" }, h("div", {}, h("strong", {}, "Storage on Torch is nearly full"),
        h("p", {}, d.quota.warnings.join(" ") + " A job that cannot save its results fails at the very end, so free up space before starting new jobs."))));
    }
    var col = h("div", {});
    if (!d.sources.queue.loaded) {
      add(col, d.connected === false ? h("h1", { class: "headline" }, "Sign in to see your jobs.") : h("h1", { class: "headline" }, "Asking Torch about your jobs"));
      if (d.sources.queue.error) add(col, h("p", { class: "subline bad" }, d.sources.queue.error));
    } else {
      var finished = d.history.length, failed = d.summary.failed, openFinished = !!S.open.finished;
      var finishedBox = h("details", { class: "finished", open: openFinished },
        h("summary", {}, h("span", { class: "sum-title" }, "Finished in the last " + plural(d.history_days, "day", "days")),
          h("span", { class: "sum-count" + (failed ? " bad" : "") }, finished ? plural(finished, "job", "jobs") + (failed ? ", " + failed + " failed" : "") : "none")),
        d.sources.history.error ? h("p", { class: "error-text" }, "The history could not be read: " + d.sources.history.error) : null,
        finished ? h("div", { class: "finished-scroll", tabindex: "0" }, h("ul", { class: "jobs" }, d.history.map(function (g) { return jobRow(g, "h"); })))
                 : h("p", { class: "empty" }, d.sources.history.loaded ? "No jobs have finished in this time." : "Loading"));
      finishedBox.addEventListener("toggle", function () { S.open.finished = finishedBox.open; });
      add(col, [headline(d.summary),
        h("div", { class: "fresh" }, h("span", { id: "tb-fresh" }, freshText(d)),
          d.connected ? h("button", { class: "link", onclick: function () { loadDash(true); } }, "Refresh") : null),
        d.sources.queue.error ? h("p", { class: "error-text" }, "The queue could not be read: " + d.sources.queue.error) : null,
        d.queue.length ? h("ul", { class: "jobs queue", "aria-label": "Jobs running and waiting" }, d.queue.map(function (g) { return jobRow(g, "q"); })) : null,
        finishedBox]);
    }
    show.apply(null, top.concat([h("div", { class: "dash" }, col, sideColumn(d))]));
    var again = document.querySelector(".finished-scroll"); if (again) again.scrollTop = scrollTop;
  }
  function freshText(d) {
    var age = d.sources.queue.age_s; if (age == null) return "";
    age += Math.round((Date.now() - S.dashAt) / 1000);
    return "Updated " + ago(age);
  }

  // ---- commands page -------------------------------------------------------------------------
  function loadCmds(reset) {
    stop("cmds"); if (S.view !== "commands") return;
    if (reset) { S.cmds = []; S.cmdLast = 0; }
    api("GET", "/api/commands?after=" + S.cmdLast + "&routine=" + (S.showRoutine ? 1 : 0)).then(function (r) {
      if (r.entries.length || reset) { S.cmds = S.cmds.concat(r.entries).slice(-400); S.cmdLast = r.last_id; drawCmds(r.log_folder); }
      else S.cmdLast = r.last_id;
      later("cmds", 4000, loadCmds);
    }, function () { later("cmds", 8000, loadCmds); });
  }
  function cmdText(e) { return "# " + e.time + "  [" + e.tag + "]  " + (e.where === "torch" ? "on Torch" : "on this Mac") + "\n$ " + e.command + "\n" + (e.stdout || "") + (e.stderr ? "\n" + e.stderr : ""); }
  function drawCmds(folder) {
    drawTop();
    var opened = {};
    Array.prototype.forEach.call(main.querySelectorAll(".cmd[open]"), function (el) { opened[el.getAttribute("data-id")] = true; });
    var list = S.cmds.slice().reverse();
    show(h("p", { class: "back" }, h("a", { href: "#help" }, "Help"), " / Activity log"),
      h("h1", { class: "page-title" }, "Activity log"),
      h("p", { class: "page-lede" }, "Every command Torch Bench sent to Torch, newest first. Select one to see exactly what was sent and what came back."),
      h("div", { class: "toolbar" },
        h("label", {}, h("input", { type: "checkbox", checked: S.showRoutine, onchange: function (e) { S.showRoutine = e.target.checked; loadCmds(true); } }), "Include the automatic status checks"),
        h("button", { class: "quiet small", onclick: function (e) { copy(list.slice(0, 20).reverse().map(cmdText).join("\n\n"), e.target); } }, "Copy the last 20"),
        folder ? h("span", { class: "hint" }, "Also saved in " + folder) : null),
      list.length ? h("div", { class: "cmds" }, list.map(function (e) {
        var bad = e.exit !== 0 && e.tag !== "started";
        var out = (e.stdout || "") + (e.stderr ? (e.stdout ? "\n" : "") + e.stderr : "");
        return h("details", { class: "cmd", "data-id": e.id, open: !!opened[e.id] },
          h("summary", {}, h("span", { class: "when" }, e.time.slice(11)), h("span", { class: "what" }, e.label || e.command),
            bad ? h("span", { class: "tag bad" }, e.tag) : null, e.where === "mac" ? h("span", { class: "tag" }, "on this Mac") : null),
          h("code", {}, e.command), out ? h("pre", { class: "raw" }, out) : h("p", { class: "hint" }, "No output."));
      })) : h("p", { class: "empty" }, S.showRoutine ? "Nothing yet." : "Nothing yet apart from the automatic status checks."));
  }

  // ---- help page -----------------------------------------------------------------------------
  function drawHelp() {
    stop("dash"); stop("cmds"); drawTop();
    var st = S.state, cfg = st.config;
    show(h("div", { class: "help" },
      h("h1", { class: "page-title" }, "Help"),
      h("p", {}, st.about),
      h("h2", {}, "NYU pages you may need"),
      h("ul", { class: "links" }, (cfg.help_links || []).map(function (l) { return h("li", {}, h("a", { href: l.url, target: "_blank", rel: "noopener" }, l.title), h("p", {}, l.note)); })),
      h("h2", {}, "Good to know about Torch"),
      h("ul", { class: "know" }, (st.good_to_know || []).map(function (k) { return h("li", {}, h("b", {}, k.title), k.text); })),
      h("h2", {}, "What Torch Bench can and cannot do"),
      h("p", {}, "By itself it only looks: the queue, your job history, your standing, storage, and your files. It cannot submit, cancel, change or delete anything on Torch. The Terminal is the one exception, on purpose: what you type there, you do as yourself."),
      h("p", {}, "It never stores a password or a code. You sign in on Microsoft's page and on your phone; Torch Bench shares that one sign-in until you choose Disconnect."),
      h("p", {}, h("a", { href: "#commands" }, "See every command Torch Bench has run")),
      h("h2", {}, "Your settings"),
      h("p", {}, "Signed in as " + cfg.netid + ", as " + (cfg.mode === "owner" ? "the person who runs the project" : "a colleague following the work") + (cfg.project_name ? ", project " + cfg.project_name : "") + "."),
      h("p", { class: "paths" }, "Settings  " + st.home + "/config.json", h("br"), "Records   " + st.home + "/logs/"),
      h("div", { class: "row" },
        h("button", { class: "quiet", onclick: function () { S.form = {}; S.wizard = 1; S.view = "setup"; drawSetup(); } }, "Change my details"),
        h("button", { class: "quiet", title: "For checking how Torch Bench reads the sign-in. One-time codes and anything typed are blanked out.", onclick: function (e) {
          api("POST", "/api/login/transcript").then(function (r) { e.target.textContent = "Saved to " + r.saved; e.target.disabled = true; }, function () { e.target.textContent = "No sign-in has happened since Torch Bench started"; e.target.disabled = true; }); } }, "Save what Torch printed at the last sign-in"),
        cfg.mode === "owner" ? h("button", { class: "quiet", onclick: function () {
          api("GET", "/api/project-file").then(function (p) {
            var a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(p, null, 2)], { type: "application/json" })), download: "torchbench-project-" + (cfg.project_name || "settings") + ".json" });
            document.body.appendChild(a); a.click(); a.remove();
          }); } }, "Save a project file for a colleague") : null),
      h("h2", {}, "Stop Torch Bench"),
      h("p", {}, "Closing this tab leaves Torch Bench running quietly on your Mac. Quitting stops it; your Torch sign-in stays open unless you disconnect first."),
      h("div", { class: "row" }, h("button", { class: "quiet", onclick: function () { api("POST", "/api/quit").then(function () { show(h("p", { class: "loading" }, "Torch Bench has stopped. You can close this tab. Start it again with the torchbench command.")); document.getElementById("tabs").hidden = true; document.getElementById("conn").hidden = true; }); } }, "Quit Torch Bench")),
      h("p", { class: "hint" }, "Torch Bench " + st.version)));
  }

  // ---- a small, safe Markdown renderer (builds page elements; never innerHTML) ----------------
  function mdInline(el, text) {
    var re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) add(el, text.slice(last, m.index));
      var t = m[0];
      if (m[1]) add(el, h("code", {}, t.slice(1, -1)));
      else if (m[2]) { var b = h("strong", {}); mdInline(b, t.slice(2, -2)); add(el, b); }
      else if (m[3]) { var i = h("em", {}); mdInline(i, t.slice(1, -1)); add(el, i); }
      else {
        var mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t);
        if (mm && /^(https:\/\/|http:\/\/|mailto:)/i.test(mm[2])) add(el, h("a", { href: mm[2], target: "_blank", rel: "noopener" }, mm[1]));
        else add(el, mm ? mm[1] : t);          // any other kind of link is shown as plain words
      }
      last = m.index + t.length;
    }
    if (last < text.length) add(el, text.slice(last));
    return el;
  }
  function mdRender(text) {
    var out = h("div", { class: "md" }), lines = text.replace(/\r/g, "").split("\n"), i = 0;
    function cells(line) { return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(function (c) { return c.trim(); }); }
    while (i < lines.length) {
      var line = lines[i], m;
      if (/^\s*$/.test(line)) { i++; continue; }
      if (/^```/.test(line)) {
        var code = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
        i++; add(out, h("pre", { class: "raw" }, code.join("\n"))); continue;
      }
      if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) { add(out, mdInline(h("h" + Math.min(6, m[1].length + 1), {}), m[2].replace(/\s+#+\s*$/, ""))); i++; continue; }
      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) { add(out, h("hr")); i++; continue; }
      if (/^\s*>/.test(line)) {
        var q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
        add(out, mdInline(h("blockquote", {}), q.join(" "))); continue;
      }
      if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        var head = cells(line), align = cells(lines[i + 1]).map(function (c) { return /:$/.test(c) ? (/^:/.test(c) ? "center" : "right") : "left"; }), rows = [];
        i += 2;
        while (i < lines.length && /\|/.test(lines[i]) && !/^\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
        add(out, h("div", { class: "table-wrap" }, h("table", { class: "grid" },
          h("thead", {}, h("tr", {}, head.map(function (c, k) { return mdInline(h("th", { scope: "col", style: "text-align:" + (align[k] || "left") }), c); }))),
          h("tbody", {}, rows.map(function (r) { return h("tr", {}, r.map(function (c, k) { return mdInline(h("td", { style: "text-align:" + (align[k] || "left") }), c); })); })))));
        continue;
      }
      if ((m = /^\s*([-*+]|\d+[.)])\s+/.exec(line))) {
        var ordered = /\d/.test(m[1]), list = h(ordered ? "ol" : "ul", {}), itemRe = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/, mi;
        while (i < lines.length && (mi = itemRe.exec(lines[i]))) {
          var textOf = mi[1]; i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !itemRe.test(lines[i])) textOf += " " + lines[i++].trim();
          add(list, mdInline(h("li", {}), textOf));
        }
        add(out, list); continue;
      }
      var para = [];
      while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,6}\s|```|\s*>|\s*([-*+]|\d+[.)])\s)/.test(lines[i])) para.push(lines[i++].trim());
      add(out, mdInline(h("p", {}), para.join(" ")));
    }
    return out;
  }
  function csvRows(text, sep) {
    var rows = [], row = [], cell = "", q = false, i, c;
    for (i = 0; i < text.length; i++) {
      c = text[i];
      if (q) { if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += c; }
      else if (c === '"') q = true;
      else if (c === sep) { row.push(cell); cell = ""; }
      else if (c === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
      else if (c !== "\r") cell += c;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }

  // ---- files page ----------------------------------------------------------------------------
  function placeOf(key) { return (S.state.places || []).filter(function (p) { return p.key === key; })[0]; }
  function splitArg() {               // "scratch/results/run 1" -> place "scratch", rel "results/run 1"
    var cut = S.arg.indexOf("/");
    return { place: cut < 0 ? S.arg : S.arg.slice(0, cut), rel: cut < 0 ? "" : S.arg.slice(cut + 1) };
  }
  function fileHref(kind, place, rel) { return "#" + kind + "/" + place + (rel ? "/" + encodeURIComponent(rel) : ""); }
  function fileQuery(place, rel) { return "root=" + encodeURIComponent(place) + "&path=" + encodeURIComponent(rel); }
  function crumbs(place, rel) {
    var parts = rel ? rel.split("/") : [], pl = placeOf(place), nav = h("nav", { class: "crumbs", "aria-label": "Folder path" });
    add(nav, h("a", { href: "#files" }, "Files"));
    add(nav, h("span", { "aria-hidden": "true" }, " / "));
    add(nav, parts.length ? h("a", { href: fileHref("files", place, "") }, pl ? pl.label : place) : h("b", {}, pl ? pl.label : place));
    parts.forEach(function (part, i) {
      add(nav, h("span", { "aria-hidden": "true" }, " / "));
      add(nav, i === parts.length - 1 ? h("b", {}, part) : h("a", { href: fileHref("files", place, parts.slice(0, i + 1).join("/")) }, part));
    });
    return nav;
  }
  function fileProblem(r) {
    if (r.not_connected) return h("div", { class: "notice flame" }, h("div", {}, h("strong", {}, "Not connected to Torch"), h("p", {}, "Sign in to look at files.")), h("button", { onclick: openSignin }, "Sign in to Torch"));
    return h("div", { class: "notice flame" }, h("div", {}, h("strong", {}, "That could not be opened"), h("p", {}, r.error)), h("a", { class: "button quiet", href: "#files" }, "Back to Files"));
  }
  function drawPlaces() {
    var places = S.state.places || [];
    show(h("h1", { class: "page-title" }, "Files"),
      h("p", { class: "page-lede" }, "Your folders on Torch. You can look and download; nothing here can change a file."),
      h("table", { class: "grid files places" }, h("tbody", {}, places.map(function (p) {
        return h("tr", {}, h("td", {}, h("a", { class: "dir", href: fileHref("files", p.key, "") }, p.label)), h("td", { class: "path" }, p.path), h("td", {}, p.note));
      }))));
  }
  function drawFiles() {
    if (!S.arg) { stop("follow"); S.fileGen++; drawPlaces(); return; }
    var at = splitArg(), place = at.place, rel = at.rel, gen = ++S.fileGen;
    stop("follow");
    if (!placeOf(place)) { show(h("h1", { class: "page-title" }, "Files"), fileProblem({ error: "That is not one of your folders." })); return; }
    show(crumbs(place, rel), h("p", { class: "loading" }, "Asking Torch for this folder"));
    api("GET", "/api/files/list?" + fileQuery(place, rel)).then(function (r) {
      if (gen !== S.fileGen || S.view !== "files") return;
      if (!r.ok) { show(crumbs(place, rel), fileProblem(r)); return; }
      var rows = r.entries.map(function (e) {
        var to = (rel ? rel + "/" : "") + e.name, folder = e.kind === "folder" || (e.kind === "link" && e.link_to === "folder");
        return h("tr", {}, h("td", {}, h("a", { class: folder ? "dir" : "", href: fileHref(folder ? "files" : "file", place, to) }, e.name + (folder ? "/" : "")),
            e.kind === "link" ? h("span", { class: "hint" }, e.link_to === "missing" ? "  broken link" : "  link") : null),
          h("td", { class: "num" }, e.size != null ? bytes(e.size) : ""), h("td", {}, e.modified));
      });
      var pull = place === "project" && rel && S.state.config.local_dir
        ? h("div", { class: "toolbar" }, h("a", { class: "button quiet small", href: "#project/pull/" + encodeURIComponent(rel) }, "Copy this folder to my Mac")) : null;
      show(crumbs(place, rel), pull,
        r.entries.length ? h("table", { class: "grid files" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Name"), h("th", { scope: "col", class: "num" }, "Size"), h("th", { scope: "col" }, "Changed"))), h("tbody", {}, rows))
                         : h("p", { class: "empty" }, "This folder is empty."),
        r.truncated ? h("p", { class: "hint" }, "Only the first 5,000 entries are shown.") : null);
    }, function (e) { if (gen === S.fileGen) show(crumbs(place, rel), fileProblem({ error: e.message })); });
  }

  function download(place, rel, button) {
    var was = button.textContent; button.disabled = true; button.textContent = "Downloading";
    fetch("/api/files/download?" + fileQuery(place, rel), { headers: { "X-Torchbench-Token": TOKEN } }).then(function (r) {
      if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || "The download failed."); });
      return r.blob();
    }).then(function (blob) {
      var a = h("a", { href: URL.createObjectURL(blob), download: rel.split("/").pop() });
      document.body.appendChild(a); a.click(); a.remove();
      button.textContent = "Downloaded"; setTimeout(function () { button.textContent = was; button.disabled = false; }, 1500);
    }).catch(function (e) { button.textContent = e.message; setTimeout(function () { button.textContent = was; button.disabled = false; }, 4000); });
  }

  function drawFile() {
    var at = splitArg(), place = at.place, rel = at.rel, gen = ++S.fileGen, name = rel.split("/").pop();
    var isLog = /\.(out|err|log)$/i.test(name), isImage = /\.(png|jpe?g|gif|webp|svg)$/i.test(name);
    stop("follow");
    var head = [crumbs(place, rel)];
    show.apply(null, head.concat([h("p", { class: "loading" }, "Asking Torch for this file")]));
    var dl = h("button", { class: "quiet small", onclick: function (e) { download(place, rel, e.target); } }, "Download");

    if (isImage) {
      fetch("/api/files/download?" + fileQuery(place, rel), { headers: { "X-Torchbench-Token": TOKEN } }).then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || "The picture could not be loaded."); });
        return r.blob();
      }).then(function (blob) {
        if (gen !== S.fileGen) return;
        show.apply(null, head.concat([h("div", { class: "toolbar" }, h("span", { class: "hint" }, bytes(blob.size)), dl), h("img", { class: "picture", alt: name, src: URL.createObjectURL(blob) })]));
      }).catch(function (e) { if (gen === S.fileGen) show.apply(null, head.concat([fileProblem({ error: e.message })])); });
      return;
    }

    function load(fromEnd) {
      api("GET", "/api/files/read?" + fileQuery(place, rel) + "&start=" + (fromEnd ? -1 : 0)).then(function (r) {
        if (gen !== S.fileGen || S.view !== "file") return;
        if (!r.ok) { show.apply(null, head.concat([fileProblem(r)])); return; }
        paint(r, fromEnd);
      }, function (e) { if (gen === S.fileGen) show.apply(null, head.concat([fileProblem({ error: e.message })])); });
    }

    function paint(r, fromEnd) {
      var bar = h("div", { class: "toolbar" }, h("span", { class: "hint" }, bytes(r.size)), dl), body, note = null;
      if (r.binary) {
        body = h("p", { class: "empty" }, "This is not a text file, so it is not shown here. Download it to open it on your Mac.");
      } else if (r.show_as === "markdown" || r.show_as === "json" || r.show_as === "csv") {
        var pretty = true, holder = h("div", {});
        var draw = function () {
          var el;
          if (!pretty) el = h("pre", { class: "raw light" }, r.text);
          else if (r.show_as === "markdown") el = mdRender(r.text);
          else if (r.show_as === "json") { try { el = h("pre", { class: "raw light" }, JSON.stringify(JSON.parse(r.text), null, 2)); } catch (x) { el = h("pre", { class: "raw light" }, r.text); } }
          else {
            var rows = csvRows(r.text, /\.tsv$/i.test(name) ? "\t" : ","), shown = rows.slice(1, 1001);
            el = h("div", { class: "table-wrap" }, h("table", { class: "grid" }, h("thead", {}, h("tr", {}, (rows[0] || []).map(function (c) { return h("th", { scope: "col" }, c); }))),
              h("tbody", {}, shown.map(function (row) { return h("tr", {}, row.map(function (c) { return h("td", { class: /^-?[\d.,]+%?$/.test(c) ? "num" : "" }, c); })); }))));
            if (rows.length > 1001) el = h("div", {}, el, h("p", { class: "hint" }, "The first 1,000 rows are shown. Download the file for all " + (rows.length - 1) + "."));
          }
          holder.replaceChildren(el);
        };
        add(bar, h("button", { class: "quiet small", onclick: function (e) { pretty = !pretty; e.target.textContent = pretty ? "Plain text" : "Formatted"; draw(); } }, "Plain text"));
        draw(); body = holder;
        if (r.more_after) note = h("p", { class: "hint" }, "Only the first " + bytes(r.end) + " of " + bytes(r.size) + " is shown. Download the file for the rest.");
      } else {
        var pre = h("pre", { class: "raw log", tabindex: "0" });
        var append = function (text, problems) {
          var map = {}; (problems || []).forEach(function (p) { map[p.line] = p; });
          var lines = text.split("\n"); if (lines[lines.length - 1] === "") lines.pop();
          lines.forEach(function (ln, k) { var p = map[k + 1]; pre.appendChild(h("span", { class: p ? "ln bad" : "ln", title: p ? p.title + ". " + p.explanation : null }, ln + "\n")); });
        };
        append(r.text, r.problems);
        var end = r.end, seen = {}, uniq = r.problems.filter(function (p) { if (seen[p.title]) return false; seen[p.title] = true; return true; });
        var summary = h("div", {});
        var paintProblems = function (list) {
          summary.replaceChildren(list.length ? h("div", { class: "notice stop" }, h("div", {}, h("strong", {}, "This log shows a problem"),
            h("ul", { class: "problems" }, list.map(function (p) { return h("li", {}, h("b", {}, p.title + ". "), p.explanation); })))) : "");
        };
        paintProblems(uniq);
        var follow = h("input", { type: "checkbox", id: "tb-follow" });
        var tick = function () {
          if (gen !== S.fileGen || S.view !== "file" || !follow.checked) return;
          if (document.hidden) { later("follow", 3000, tick); return; }
          api("GET", "/api/files/read?" + fileQuery(place, rel) + "&start=" + end + "&follow=1").then(function (n) {
            if (gen !== S.fileGen || !n.ok) { if (n && !n.ok) follow.checked = false; return; }
            if (n.start !== end) { load(true); return; }          // the file was replaced: start again from its end
            if (n.text) {
              var stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
              append(n.text, n.problems);
              n.problems.forEach(function (p) { if (!seen[p.title]) { seen[p.title] = true; uniq.push(p); } });
              paintProblems(uniq);
              if (stick) pre.scrollTop = pre.scrollHeight;
            }
            end = n.end; later("follow", 3000, tick);
          }, function () { later("follow", 6000, tick); });
        };
        follow.addEventListener("change", function () { if (follow.checked) { pre.scrollTop = pre.scrollHeight; tick(); } else stop("follow"); });
        add(bar, [h("label", { for: "tb-follow" }, follow, "Follow new lines"),
          h("button", { class: "quiet small", onclick: function () { stop("follow"); load(!fromEnd); } }, fromEnd ? "From the top" : "Jump to the end")]);
        body = h("div", {}, summary, pre);
        if (fromEnd && r.more_before) note = h("p", { class: "hint" }, "Showing the last " + bytes(r.end - r.start) + " of " + bytes(r.size) + ".");
        if (!fromEnd && r.more_after) note = h("p", { class: "hint" }, "Showing the first " + bytes(r.end) + " of " + bytes(r.size) + ".");
        setTimeout(function () { if (fromEnd) pre.scrollTop = pre.scrollHeight; }, 0);
      }
      show.apply(null, head.concat([bar, note, body].filter(Boolean)));
    }
    load(isLog);
  }

  // ---- project page: two folders, push and pull ----------------------------------------------
  function drawProject() {
    var gen = ++S.projGen;
    stop("sync");
    if (!S.projShown) show(h("p", { class: "loading" }, "Looking at the project's folders"));
    api("GET", "/api/project").then(function (st) {
      if (gen !== S.projGen || S.view !== "project") return;
      S.projShown = true;
      if (!st.configured || S.projEdit) { projectForm(st); return; }
      projectHome(st);
    }, function (e) { show(h("div", { class: "notice stop" }, h("div", {}, h("strong", {}, "The project could not be read"), h("p", {}, e.message)))); });
  }

  function projectForm(st) {
    var editing = st.configured, d = st.defaults, err = h("p", { class: "error-text", role: "alert" });
    var name = h("input", { type: "text", id: "tb-pname", value: st.name || "", placeholder: "my-project", autocomplete: "off", spellcheck: "false" });
    var local = h("input", { type: "text", id: "tb-plocal", class: "wide", value: st.local_dir || "", autocomplete: "off", spellcheck: "false" });
    var remote = h("input", { type: "text", id: "tb-premote", class: "wide", value: st.remote_dir || "", autocomplete: "off", spellcheck: "false" });
    var account = h("input", { type: "text", id: "tb-pacct", value: st.sbatch_account || "", placeholder: "torch_pr_000_general", autocomplete: "off", spellcheck: "false" });
    var touchedLocal = !!st.local_dir, touchedRemote = !!st.remote_dir;
    function fill() {
      var n = name.value.trim();
      if (!touchedLocal) local.value = n ? d.local_parent + "/" + n : "";
      if (!touchedRemote) remote.value = n ? d.remote_parent + "/" + n : "";
    }
    name.addEventListener("input", fill);
    local.addEventListener("input", function () { touchedLocal = true; });
    remote.addEventListener("input", function () { touchedRemote = true; });
    var choose = st.can_choose_folder ? h("button", { type: "button", class: "quiet", onclick: function (e) {
      e.target.disabled = true;
      api("POST", "/api/project/choose-folder", { start: local.value }).then(function (r) {
        e.target.disabled = false;
        if (r.path) { local.value = r.path; touchedLocal = true; } else if (r.error) err.textContent = r.error;
      }, function (x) { e.target.disabled = false; err.textContent = x.message; });
    } }, "Choose") : null;
    var form = h("form", { class: "panel narrow", onsubmit: function (e) {
        e.preventDefault(); err.textContent = "";
        api("POST", "/api/project/save", { project_name: name.value, local_dir: local.value, remote_dir: remote.value, sbatch_account: account.value }).then(function (r) {
          S.projEdit = false;
          S.projNote = (r.created_local ? "Created " + r.local_dir + " on this Mac. " : "") + (r.imported_excludes ? "This folder has its own push.sh, so a push will leave out the same files that script does." : "");
          api("GET", "/api/state").then(function (s2) { S.state = s2; drawTop(); });
          projectHome(r);
        }, function (x) { err.textContent = x.message; });
      } },
      h("h1", {}, editing ? "Project name and folders" : "Set up a project"),
      h("p", {}, "A project is a folder of code on this Mac and its copy on Torch. Code goes from the Mac to Torch; results come back."),
      h("div", { class: "field" }, h("label", { for: "tb-pname" }, "Project name"), name),
      h("div", { class: "field" }, h("label", { for: "tb-plocal" }, "Folder on this Mac ", h("span", { class: "hint" }, "created if it does not exist")),
        h("div", { class: "row" }, local, choose)),
      h("div", { class: "field" }, h("label", { for: "tb-premote" }, "Folder on Torch ", h("span", { class: "hint" }, "inside your scratch, home or archive; your first push creates it")), remote),
      h("div", { class: "field" }, h("label", { for: "tb-pacct" }, "SLURM account ", h("span", { class: "hint" }, "optional; the allocation jobs are charged to")), account),
      err,
      h("div", { class: "row" }, h("button", { type: "submit" }, editing ? "Save" : "Create project"),
        editing ? h("button", { type: "button", class: "quiet", onclick: function () { S.projEdit = false; drawProject(); } }, "Cancel") : null));
    show(form);
    if (!editing) setTimeout(function () { name.focus(); }, 0);
  }

  function projectHome(st) {
    var panel = h("div", { id: "tb-sync", "aria-live": "polite" });
    var torchState = st.remote_exists === true ? "ready" : st.remote_exists === false ? "not there yet; your first push creates it" : "sign in to check";
    var macState = st.local_exists ? (st.local_empty ? "ready, empty so far" : "ready") : "missing";
    var pushBtn = h("button", { onclick: function () { preview("push", ""); }, disabled: !st.can_push, title: st.can_push ? null : "Pushing is for the person who runs the project" }, "Push code to Torch");
    var pullBtn = h("button", { class: st.can_push ? "quiet" : "", onclick: function () { preview("pull", ""); } }, "Pull results to this Mac");
    show(h("h1", { class: "page-title" }, st.name),
      S.projNote ? h("p", { class: "ok-line" }, S.projNote) : null,
      h("table", { class: "grid places pair" }, h("tbody", {},
        h("tr", {}, h("td", {}, "On this Mac"), h("td", { class: "path" }, st.local_dir), h("td", {}, macState)),
        h("tr", {}, h("td", {}, "On Torch"), h("td", { class: "path" }, st.remote_dir), h("td", {}, torchState)))),
      h("div", { class: "row sync-actions" }, pushBtn, pullBtn,
        h("button", { class: "link", onclick: function () { S.projEdit = true; S.projNote = ""; drawProject(); } }, "Change name or folders")),
      h("p", { class: "hint" }, (st.can_push ? "Push sends your code to Torch and replaces files there that differ. " : "Pushing code is for the person who runs the project. ")
        + "Pull brings back " + st.pull_paths.join(" and ") + ", without model weights. Both show you the list first and delete nothing."),
      panel);
    S.projNote = "";

    function fail(message, r) {
      panel.replaceChildren(h("div", { class: "notice flame" }, h("div", {}, h("strong", {}, r && r.not_connected ? "Not connected to Torch" : "That did not work"), h("p", {}, message)),
        r && r.not_connected ? h("button", { onclick: openSignin }, "Sign in to Torch") : null));
    }
    function preview(direction, rel) {
      panel.replaceChildren(h("p", {}, h("span", { class: "pulse" }), "Checking what would be copied. Nothing is copied yet."));
      api("POST", "/api/sync/preview", { direction: direction, rel: rel }).then(function (r) { r.ok ? showPreview(r) : fail(r.error, r); }, function (x) { fail(x.message); });
    }
    function showPreview(r) {
      var push = r.direction === "push", n = r.count, size = r.bytes != null && n ? " (" + bytes(r.bytes) + ")" : "";
      var where = push ? "to Torch" : "to this Mac";
      var kids = [];
      if (r.changed) kids.push(h("p", { class: "error-text" }, "The files changed after the preview, so nothing was copied. This is the new list."));
      if (!n) {
        kids.push(h("h2", {}, push ? "Nothing to push" : "Nothing to pull"),
          h("p", {}, push ? "Torch already has every file in the Mac folder." : (r.not_there.length ? "There is no " + r.not_there.join(" or ") + " folder in the project on Torch yet." : "This Mac already has everything.")),
          h("div", { class: "row" }, h("button", { class: "quiet", onclick: function () { panel.replaceChildren(); } }, "Close")));
      } else {
        kids.push(h("h2", {}, (push ? "A push would send " : "A pull would copy ") + plural(n, "file", "files") + size + " " + where),
          h("p", {}, push ? "Files with the same name on Torch are replaced, even ones edited there. Nothing on Torch is deleted."
                          : "Files with the same name on this Mac are replaced. Nothing is deleted, and model weights stay on Torch."),
          h("pre", { class: "raw light filelist", tabindex: "0" }, r.files.join("\n") + (r.more ? "\n… and " + r.more + " more" : "")),
          !push && r.not_there.length ? h("p", { class: "hint" }, "Not on Torch yet: " + r.not_there.join(", ") + ".") : null,
          h("details", { class: "explain" }, h("summary", {}, "What is left out, and the exact command"),
            h("p", {}, "Left out: " + r.left_out.join(", ") + "."), h("pre", { class: "raw" }, r.commands.join("\n\n"))),
          h("div", { class: "row" }, h("button", { onclick: function (e) {
              e.target.disabled = true;
              api("POST", "/api/sync/start", { plan: r.plan }).then(function (s2) {
                if (s2.started) watch();
                else if (s2.changed) showPreview(s2);
                else if (s2.nothing) preview(r.direction, r.rel);
                else fail(s2.error, s2);
              }, function (x) { fail(x.message); });
            } }, (push ? "Push " : "Pull ") + plural(n, "file", "files")),
            h("button", { class: "quiet", onclick: function () { panel.replaceChildren(); } }, "Cancel")));
      }
      panel.replaceChildren(h("div", { class: "sync-box" }, kids.filter(Boolean)));
    }
    function watch() {
      api("GET", "/api/sync/job").then(function (j) {
        if (S.view !== "project" || !j.active) return;
        var push = j.direction === "push", out = h("pre", { class: "raw filelist" }, j.output || "Starting");
        if (!j.done) {
          panel.replaceChildren(h("div", { class: "sync-box" }, h("h2", {}, h("span", { class: "pulse" }), push ? "Pushing to Torch" : "Pulling to this Mac"), out,
            h("div", { class: "row" }, h("button", { class: "quiet", onclick: function () { api("POST", "/api/sync/cancel"); } }, "Stop"))));
          out.scrollTop = out.scrollHeight;
          later("sync", 1000, watch);
        } else {
          var good = j.exit === 0;
          panel.replaceChildren(h("div", { class: "sync-box" },
            h("h2", {}, good ? (push ? "Pushed " : "Pulled ") + plural(j.count, "file", "files") + (j.seconds >= 2 ? " in " + dur(j.seconds) : "") + (push ? " to Torch" : " to this Mac") : j.cancelled ? "Stopped before it finished" : "The copy did not finish"),
            good ? null : h("p", {}, "Files copied before it stopped are in place; run it again to finish. The output below says what happened."),
            h("details", { class: "explain", open: !good }, h("summary", {}, "What rsync printed"), out),
            h("div", { class: "row" }, h("button", { class: "quiet", onclick: function () { drawProject(); } }, "Done"))));
        }
      }, function () { later("sync", 2000, watch); });
    }
    // Coming back to the page while a copy runs, or arriving from "Copy this folder to my Mac".
    api("GET", "/api/sync/job").then(function (j) {
      if (j.active && !j.done) watch();
      else if (S.arg.indexOf("pull/") === 0) { var rel = S.arg.slice(5); S.arg = ""; history.replaceState(null, "", "#project"); preview("pull", rel); }
    }, function () {});
  }

  // ---- terminal page -------------------------------------------------------------------------
  function drawTerminal() {
    var gen = ++S.termGen, box = h("div", { class: "term-box" }), status = h("p", { class: "hint", role: "status" });
    var closeBtn = h("button", { class: "quiet small", onclick: function () { api("POST", "/api/term/close").catch(function () {}); } }, "End this shell");
    show(h("div", { class: "term-head" }, h("p", { class: "hint" }, "A shell on Torch's login node, as you. Quick commands only; heavy work belongs in a job. The session is recorded on your Mac."),
        h("div", { class: "toolbar" }, status, closeBtn)), box);
    if (typeof Terminal === "undefined") { status.textContent = "The terminal part of Torch Bench did not load. Reload the page."; return; }
    var term = new Terminal({ fontFamily: '"SF Mono", Menlo, Consolas, "Liberation Mono", monospace', fontSize: 13, cursorBlink: true, scrollback: 8000,
      theme: { background: "#1F1433", foreground: "#EDE7F6", cursor: "#F6A53B", selectionBackground: "#6B4A8E" } });
    var fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open(box);
    try { fit.fit(); } catch (x) {}
    S.term = term;
    var alive = function () { return gen === S.termGen && S.view === "terminal"; };
    var pending = "", sending = false;
    function flush() {
      if (sending || !pending) return;
      var data = pending; pending = ""; sending = true;
      api("POST", "/api/term/write", { data: data }).then(function () { sending = false; flush(); }, function () { sending = false; });
    }
    term.onData(function (d) { pending += d; flush(); });
    var resizeTimer = null;
    term.onResize(function (sz) { clearTimeout(resizeTimer); resizeTimer = setTimeout(function () { api("POST", "/api/term/resize", { rows: sz.rows, cols: sz.cols }).catch(function () {}); }, 150); });
    var onWin = function () { if (alive()) { try { fit.fit(); } catch (x) {} } else window.removeEventListener("resize", onWin); };
    window.addEventListener("resize", onWin);

    function read(since) {
      if (!alive()) return;
      api("GET", "/api/term/read?since=" + since).then(function (r) {
        if (!alive()) return;
        if (r.data) { var bin = atob(r.data), arr = new Uint8Array(bin.length), i; for (i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i); term.write(arr); }
        if (r.closed) { ended(); return; }
        read(r.next);
      }, function () { if (alive()) setTimeout(function () { read(since); }, 2000); });
    }
    function ended() {
      closeBtn.hidden = true;
      status.replaceChildren("The shell has ended. ", h("button", { class: "link", onclick: function () { drawTerminal(); } }, "Open a new one"));
    }
    function begin() {
      status.textContent = "Opening a shell on Torch";
      api("POST", "/api/term/start", { rows: term.rows, cols: term.cols }).then(function (r) {
        if (!alive()) return;
        status.textContent = r.reattached ? "Back in the shell you left open." : "";
        if (r.reattached) api("POST", "/api/term/resize", { rows: term.rows, cols: term.cols }).catch(function () {});
        term.focus(); read(0);
      }, function (e) {
        if (!alive()) return;
        closeBtn.hidden = true;
        status.replaceChildren(e.message + " ", h("button", { class: "link", onclick: function () { openSignin(); } }, "Sign in"), " then ", h("button", { class: "link", onclick: function () { drawTerminal(); } }, "try again"), ".");
      });
    }
    begin();
  }

  // ---- routing -------------------------------------------------------------------------------
  function route() {
    var st = S.state;
    if (!st.configured) { S.view = "setup"; drawSetup(); return; }
    if (!st.ssh_ready && S.view !== "setup") { S.view = "setup"; S.wizard = 2; drawSetup(); return; }
    if (S.view === "setup") { drawSetup(); return; }
    var hash = (location.hash || "#jobs").slice(1), cut = hash.indexOf("/");
    var want = cut < 0 ? hash : hash.slice(0, cut);
    try { S.arg = cut < 0 ? "" : decodeURIComponent(hash.slice(cut + 1)); } catch (x) { S.arg = ""; }
    S.view = ["jobs", "project", "files", "file", "terminal", "commands", "help"].indexOf(want) >= 0 ? want : "jobs";
    stop("dash"); stop("cmds"); stop("follow"); S.fileGen++; S.termGen++;
    if (S.term && S.view !== "terminal") { try { S.term.dispose(); } catch (x) {} S.term = null; }
    stop("sync");
    if (S.view === "project") { drawTop(); S.projShown = false; drawProject(); return; }
    if (S.view === "files") { drawTop(); drawFiles(); return; }
    if (S.view === "file") { drawTop(); drawFile(); return; }
    if (S.view === "terminal") { drawTop(); drawTerminal(); return; }
    if (S.view === "jobs") { S.dashSig = ""; if (S.dash) drawJobs(true); else show(h("p", { class: "loading" }, "Asking Torch about your jobs")); drawTop(); loadDash(); }
    else if (S.view === "commands") { show(h("p", { class: "loading" }, "Loading")); loadCmds(true); }
    else drawHelp();
  }
  window.addEventListener("hashchange", function () { if (S.state && S.view !== "setup") route(); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden && S.view === "jobs" && S.state && S.state.configured) loadDash(); });

  api("GET", "/api/state").then(function (st) {
    S.state = st;
    if (st.config_problem) { show(h("div", { class: "notice stop" }, h("div", {}, h("strong", {}, "The settings file could not be read"), h("p", {}, st.config_problem + " Fix or remove the file, then reload this page.")))); return; }
    route();
  }, function (e) { show(h("p", { class: "loading" }, e.message)); });
})();
