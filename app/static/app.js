/* Torch Bench window. Plain JavaScript, no libraries. All text reaches the page through
   textContent (never innerHTML), so nothing Torch prints can run as code. */
(function () {
  "use strict";

  var TOKEN = document.querySelector('meta[name="torchbench-token"]').content;
  var main = document.getElementById("main");
  var S = {
    state: null, view: "jobs", dash: null, dashSig: "", dashAt: 0, open: {}, details: {}, disk: null,
    wizard: 0, form: {}, cmds: [], cmdLast: 0, showRoutine: false, timers: {}, signin: null
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
  function show(el) { main.replaceChildren.apply(main, Array.prototype.slice.call(arguments)); }
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
      if (a.getAttribute("data-view") === S.view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
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
          radio("colleague", "I want to follow the work", "Watch jobs, read results and logs. Nothing you do here can change the project."),
          radio("owner", "I run this project", "Everything above; later versions add submitting and cancelling jobs.")),
        h("div", { class: "field" }, h("label", { for: "tb-file" }, "Project file ", h("span", { class: "hint" }, "optional. The person who runs the project can give you one; it fills in the rest.")), file, imported),
        h("details", { class: "more", open: !!(f.project_name || f.remote_dir || f.sbatch_account) }, h("summary", {}, "Project details (optional; you can add them later in the settings file)"),
          h("div", {},
            h("div", { class: "field" }, h("label", { for: "tb-proj" }, "Project name"), inp("tb-proj", "project_name", "my-project")),
            h("div", { class: "field" }, h("label", { for: "tb-rdir" }, "Project folder on Torch ", h("span", { class: "hint" }, "leave empty for /scratch/your-netid/project-name")), inp("tb-rdir", "remote_dir", "/scratch/abc1234/my-project")),
            h("div", { class: "field" }, h("label", { for: "tb-acct" }, "SLURM account ", h("span", { class: "hint" }, "the allocation jobs are charged to")), inp("tb-acct", "sbatch_account", "torch_pr_000_general")),
            h("div", { class: "field" }, h("label", { for: "tb-ldir" }, "Project folder on this Mac"), inp("tb-ldir", "local_dir", "~/Desktop/my-project")))),
        err,
        h("div", { class: "row" }, h("button", { onclick: function () {
          var body = { netid: f.netid, mode: f.mode, project_name: f.project_name, remote_dir: f.remote_dir, sbatch_account: f.sbatch_account, local_dir: f.local_dir };
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
    var meta = "Job " + g.id + (g.partition ? " on " + g.partition : "") + (g.gpus ? ", " + plural(g.gpus, "GPU", "GPUs") + (g.is_array ? " each" : "") : "");
    var side;
    if (list === "q") {
      if (g.is_array || g.total > 1) {
        var r = g.counts.running || 0, w = g.counts.waiting || 0;
        side = [h("div", { class: "bar", role: "img", "aria-label": r + " running, " + w + " waiting" }, h("i", { style: "width:" + (100 * r / g.total) + "%" }), h("i", { class: "w", style: "width:" + (100 * w / g.total) + "%" })),
          countsText(g.counts), g.array_limit ? h("div", {}, "at most " + g.array_limit + " at once") : null];
      } else if (g.kind === "running" && first.limit_s) {
        side = [h("div", { class: "bar", role: "img", "aria-label": "time used" }, h("i", { style: "width:" + Math.min(100, 100 * (first.elapsed_s || 0) / first.limit_s) + "%" })),
          dur(first.elapsed_s) + " of " + dur(first.limit_s) + " allowed"];
      } else if (g.waited) { side = ["waiting " + g.waited + " so far"]; }
    } else {
      side = [g.ended ? "ended " + g.ended : "", g.longest ? h("div", {}, (g.total > 1 ? "longest task " : "ran ") + g.longest) : null];
    }
    var why = g.why ? h("div", { class: "why" + (g.why.stuck ? " stuck" : "") }, h("b", {}, g.why.title + ". "), g.why.detail,
      g.scheduler_estimate ? h("div", { class: "why-more" }, g.scheduler_estimate + " Estimates move as other jobs come and go.") : null) : null;
    var head = h("button", { class: "job-head", "aria-expanded": open ? "true" : "false", onclick: function () { S.open[key] = !open; drawJobs(true); } },
      h("span", { class: "state " + g.kind }, chip),
      h("span", {}, h("div", { class: "job-name" }, g.name || "(no name)"), h("div", { class: "job-meta" }, meta + (list === "h" && g.total > 1 ? ". " + countsText(g.counts) : "")), why),
      h("span", { class: "job-side" }, side),
      h("span", { class: "chev", "aria-hidden": "true" }, "›"));
    var li = h("li", { class: "job" }, head);
    if (open) { li.setAttribute("open-row", ""); li.appendChild(jobBody(g, list)); }
    return li;
  }

  function jobBody(g, list) {
    var rows = g.tasks.map(function (t) {
      return h("tr", {}, h("td", {}, t.id), h("td", { class: taskClass(t.kind) }, t.state_word + (t.note ? " " + t.note : "")),
        list === "q" ? [h("td", {}, t.kind === "running" ? t.elapsed : ""), h("td", {}, t.limit), h("td", {}, t.node || (t.task_count > 1 ? t.task_count + " tasks" : ""))]
                     : [h("td", {}, t.elapsed), h("td", {}, t.ended), h("td", {}, t.exit_code && t.exit_code !== "0:0" ? "exit " + t.exit_code : "")]);
    });
    var heads = list === "q" ? ["Job", "State", "Time used", "Time allowed", "Machine"] : ["Job", "Result", "Ran for", "Ended", ""];
    var body = h("div", { class: "job-body" },
      h("table", { class: "tasks" }, h("thead", {}, h("tr", {}, heads.map(function (x) { return h("th", { scope: "col" }, x); }))), h("tbody", {}, rows)),
      g.hidden_tasks ? h("p", { class: "hint" }, "and " + g.hidden_tasks + " more") : null);
    if (g.priority) {
      var p = g.priority;
      body.appendChild(h("p", { class: "prio" }, "Priority " + Math.round(p.priority) + " is made of: fair-share " + Math.round(p.fairshare) + ", time waiting " + Math.round(p.age) + ", partition " + Math.round(p.partition) + ", job size " + Math.round(p.jobsize) + ", QOS " + Math.round(p.qos) + ". Higher starts sooner."));
    }
    var id = (g.tasks[0] && /^\d+(_\d+)?$/.test(g.tasks[0].id)) ? g.tasks[0].id : g.id;
    var det = S.details[id];
    var slot = h("div", { class: "detail" });
    if (!det) {
      slot.appendChild(h("button", { class: "quiet small", onclick: function (e) {
        e.target.disabled = true; e.target.textContent = "Asking Torch";
        api("GET", "/api/job?id=" + encodeURIComponent(id)).then(function (d) { S.details[id] = d; drawJobs(true); }, function (x) { S.details[id] = { ok: false, error: x.message }; drawJobs(true); });
      } }, "Show the scheduler's details for " + id));
    } else if (!det.ok) {
      slot.appendChild(h("p", { class: "hint" }, det.error));
    } else {
      slot.appendChild(h("dl", { class: "kv" }, det.fields.map(function (f) { return [h("dt", {}, f.key), h("dd", {}, f.value)]; })));
      slot.appendChild(h("details", { class: "more" }, h("summary", {}, "Exactly what Torch said"), h("div", {}, h("pre", { class: "raw" }, det.raw))));
    }
    body.appendChild(slot);
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
    // standing
    kids.push(h("section", {}, h("h2", {}, "Your place in line"),
      f.value == null ? h("p", {}, d.sources.fairshare.error || "Not loaded yet.")
        : [h("div", { class: "big" }, f.value.toFixed(2), " ", h("small", {}, "fair-share, from 0 to 1")),
           h("div", { class: "meter", role: "img", "aria-label": "fair-share " + f.value.toFixed(2) }, h("i", { style: "width:" + Math.max(2, 100 * f.value) + "%" })),
           h("p", { class: "strong" }, f.title), h("p", {}, f.detail), f.account ? h("p", {}, "Account " + f.account) : null]));
    // storage
    var storage = h("section", {}, h("h2", {}, "Storage on Torch"));
    if (q.parsed) {
      q.filesystems.forEach(function (fs) {
        var warn = fs.worst_pct >= q.warn_percent;
        add(storage, h("div", { class: "fs" },
          h("div", { class: "fs-row" }, h("b", {}, fs.fs), h("span", {}, fs.space_used + (fs.space_limit ? " of " + fs.space_limit : "") + " (" + Math.round(fs.space_pct) + "%)")),
          h("div", { class: "meter" + (warn ? " warn" : ""), role: "img", "aria-label": fs.fs + " " + Math.round(fs.space_pct) + " percent full" }, h("i", { style: "width:" + Math.min(100, fs.space_pct) + "%" })),
          fs.files_pct != null ? h("div", { class: "fs-row hint" }, h("span", {}, "files"), h("span", {}, fs.files_used + (fs.files_limit ? " of " + fs.files_limit : "") + " (" + Math.round(fs.files_pct) + "%)")) : null));
      });
      add(storage, h("p", {}, "From Torch's quota report" + (d.sources.quota.age_s != null ? ", read " + ago(d.sources.quota.age_s) : "") + ". The report can lag behind by hours."));
    } else if (q.raw) {
      add(storage, [h("p", {}, "Torch Bench could not read the quota report, so here it is as Torch printed it."), h("pre", { class: "raw" }, q.raw)]);
    } else {
      add(storage, h("p", {}, d.sources.quota.error || "Not loaded yet."));
    }
    if (d.remote_dir) {
      add(storage, S.disk && S.disk.ok ? h("p", { class: "strong" }, S.disk.folder + " holds " + S.disk.size + " (measured at " + S.disk.measured + ")")
        : h("p", {}, h("button", { class: "link", onclick: function (e) { e.target.disabled = true; e.target.textContent = "Measuring (can take a minute)"; api("POST", "/api/disk").then(function (r) { S.disk = r; if (!r.ok) { e.target.textContent = r.error; } else drawJobs(true); }, function (x) { e.target.textContent = x.message; }); } }, "Measure the project folder now")));
    }
    kids.push(storage);
    // partitions
    kids.push(h("section", {}, h("h2", {}, "How busy Torch is"),
      h("table", { class: "parts" }, h("tbody", {}, d.partitions.map(function (p) {
        var free = p.readable && p.role === "GPU" ? p.gpus_free + " of " + p.gpus_total + " GPUs free" : p.readable ? p.nodes_usable + " of " + p.nodes + " machines taking work" : "no reading";
        var wait = p.typical_wait ? "your jobs lately started within about " + p.typical_wait : (p.wait_samples ? "too few recent jobs to estimate a wait" : "no recent jobs of yours here");
        return h("tr", {}, h("td", {}, h("div", { class: "pname" }, p.partition), h("div", { class: "hint" }, wait)), h("td", {}, free));
      }))),
      h("p", {}, "Wait times are estimates from your own jobs in the last 7 days. A free GPU does not always mean no wait.")));
    // good to know
    kids.push(h("section", {}, h("h2", {}, "Good to know"), h("ul", { class: "know" }, d.good_to_know.map(function (k) { return h("li", {}, h("b", {}, k.title), k.text); }))));
    return h("aside", { class: "side", "aria-label": "Standing, storage and cluster load" }, kids);
  }

  function drawJobs(forceDraw) {
    var d = S.dash; if (!d || S.view !== "jobs") return;
    var sig = JSON.stringify([d.connected, d.summary, d.queue, d.history, d.fairshare, d.quota, d.partitions, d.sources.queue.error, d.sources.history.error]);
    drawTop();
    var fresh = document.getElementById("tb-fresh");
    if (!forceDraw && sig === S.dashSig && fresh) { fresh.textContent = freshText(d); return; }
    S.dashSig = sig;
    var top = [];
    if (d.connected === false) {
      top.push(h("div", { class: "notice flame", role: "status" }, h("div", {}, h("strong", {}, "Not connected to Torch"),
        h("p", {}, d.sources.queue.loaded ? "What you see below is the last answer Torch gave, " + ago(d.sources.queue.age_s) + ". Sign in to bring it up to date." : "Sign in to see your jobs. It takes about a minute and needs your phone for Duo.")),
        h("button", { onclick: openSignin }, "Sign in to Torch")));
    }
    if (d.quota.warnings.length) {
      top.push(h("div", { class: "notice stop", role: "alert" }, h("div", {}, h("strong", {}, "Storage on Torch is nearly full"),
        h("p", {}, d.quota.warnings.join(" ") + " A job that cannot save its results fails at the very end. Free up space before starting new jobs."))));
    }
    var col = h("div", {});
    if (!d.sources.queue.loaded) {
      add(col, d.connected === false ? h("h1", { class: "headline" }, "Sign in to see your jobs.") : h("h1", { class: "headline" }, "Asking Torch about your jobs"));
      if (d.sources.queue.error) add(col, h("p", { class: "subline bad" }, d.sources.queue.error));
    } else {
      add(col, [headline(d.summary), h("p", { class: "subline" + (d.summary.failed ? " bad" : "") }, d.summary.subline),
        h("div", { class: "fresh" }, h("span", { id: "tb-fresh" }, freshText(d)),
          d.connected ? h("button", { class: "link", onclick: function () { loadDash(true); } }, "Refresh now") : null),
        d.sources.queue.error ? h("p", { class: "error-text" }, "The queue could not be read: " + d.sources.queue.error) : null,
        h("h2", { class: "section-title" }, "In the queue"),
        d.queue.length ? h("ul", { class: "jobs" }, d.queue.map(function (g) { return jobRow(g, "q"); })) : h("p", { class: "empty" }, "No jobs are running or waiting right now."),
        h("h2", { class: "section-title" }, "Finished in the last " + plural(d.history_days, "day", "days")),
        d.sources.history.error ? h("p", { class: "error-text" }, "The history could not be read: " + d.sources.history.error) : null,
        d.history.length ? h("ul", { class: "jobs" }, d.history.map(function (g) { return jobRow(g, "h"); })) : h("p", { class: "empty" }, d.sources.history.loaded ? "No jobs have finished in this time." : "Loading")]);
    }
    show.apply(null, top.concat([h("div", { class: "dash" }, col, sideColumn(d))]));
  }
  function freshText(d) {
    var age = d.sources.queue.age_s; if (age == null) return "";
    age += Math.round((Date.now() - S.dashAt) / 1000);
    return "Queue read " + ago(age) + (d.connected ? "; it refreshes every 30 seconds while this window is open." : ".");
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
    Array.prototype.forEach.call(main.querySelectorAll(".cmd details[open]"), function (el) { opened[el.getAttribute("data-id")] = true; });
    var list = S.cmds.slice().reverse();
    show(h("h1", { class: "page-title" }, "Commands Torch Bench ran"),
      h("p", { class: "page-lede" }, "Every command sent to Torch, and what came back. Nothing happens on the cluster that is not listed here. The same record is kept in " + (folder || "the log folder") + "."),
      h("div", { class: "toolbar" },
        h("label", {}, h("input", { type: "checkbox", checked: S.showRoutine, onchange: function (e) { S.showRoutine = e.target.checked; loadCmds(true); } }), "Show routine status checks"),
        h("button", { class: "quiet small", onclick: function (e) { copy(list.slice(0, 20).reverse().map(cmdText).join("\n\n"), e.target); } }, "Copy the last 20")),
      list.length ? h("ul", { class: "cmds" }, list.map(function (e) {
        var bad = e.exit !== 0 && e.tag !== "started";
        var out = (e.stdout || "") + (e.stderr ? (e.stdout ? "\n" : "") + e.stderr : "");
        return h("li", { class: "cmd" },
          h("div", { class: "cmd-top" }, h("span", { class: "what" }, e.label || "Command"), h("span", {}, e.time),
            h("span", { class: "tag" + (e.where === "torch" ? " torch" : "") }, e.where === "torch" ? "on Torch" : "on this Mac"),
            h("span", { class: "tag" + (bad ? " bad" : "") }, e.tag), e.seconds ? h("span", {}, e.seconds + " s") : null),
          h("code", {}, e.command),
          out ? h("details", { "data-id": e.id, open: !!opened[e.id] }, h("summary", {}, "Output (" + plural(out.split("\n").length, "line", "lines") + ")"), h("pre", { class: "raw" }, out)) : null);
      })) : h("p", { class: "empty" }, S.showRoutine ? "No commands yet." : "No commands yet, apart from routine status checks. Tick the box above to see those."));
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
      h("h2", {}, "What Torch Bench does and does not do"),
      h("p", {}, "This version only looks: it reads the queue, your job history, your fair-share standing, GPU availability and your storage quota. It cannot submit, cancel, change or delete anything on Torch."),
      h("p", {}, "It never stores a password or a code. Signing in happens on Microsoft's page and on your phone; Torch Bench then shares that one sign-in, which you can close at any time with Disconnect."),
      h("p", {}, "Every command it runs is listed on the Commands page."),
      h("h2", {}, "Your settings"),
      h("p", {}, "Signed in as " + cfg.netid + ", as " + (cfg.mode === "owner" ? "the person who runs the project" : "a colleague following the work") + (cfg.project_name ? ", project " + cfg.project_name : "") + "."),
      h("p", { class: "paths" }, "Settings  " + st.home + "/config.json", h("br"), "Logs      " + st.home + "/logs/torch_shell/"),
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

  // ---- routing -------------------------------------------------------------------------------
  function route() {
    var st = S.state;
    if (!st.configured) { S.view = "setup"; drawSetup(); return; }
    if (!st.ssh_ready && S.view !== "setup") { S.view = "setup"; S.wizard = 2; drawSetup(); return; }
    if (S.view === "setup") { drawSetup(); return; }
    var want = (location.hash || "#jobs").slice(1);
    S.view = ["jobs", "commands", "help"].indexOf(want) >= 0 ? want : "jobs";
    stop("dash"); stop("cmds");
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
