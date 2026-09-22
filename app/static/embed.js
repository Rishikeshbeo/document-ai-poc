/* Document AI — drop-in panel loader.
 *
 *   <div id="docai"></div>
 *   <script src="https://docai.example/embed.js"></script>
 *   <script>
 *     DocAI.mount('#docai', {
 *       token: TOKEN,                     // from POST /api/session on your server
 *       dropTarget: document.body,        // optional: drop a file anywhere on the page
 *       onAccept: json => fillForm(json)  // user pressed "Use this data"
 *     });
 *   </script>
 *
 * The handle it returns also has read(file), for a file that arrives some other
 * way — your own file input, a paste, a fetch.
 *
 * The service origin is taken from this script's own src, so the host never
 * configures a URL and every inbound message is checked against it. No
 * dependencies, no build step, safe to include more than once.
 */
(function () {
  'use strict';

  if (window.DocAI && window.DocAI.mount) return;   // already loaded

  var ORIGIN = (function () {
    var s = document.currentScript;
    if (!s) {
      var all = document.getElementsByTagName('script');
      s = all[all.length - 1];
    }
    try { return new URL(s.src, window.location.href).origin; }
    catch (e) { return window.location.origin; }
  })();

  var EVENTS = ['ready', 'result', 'corrected', 'accept', 'save', 'error', 'resize'];

  // The wire name stays "docai:accepted" — it is the documented contract and
  // pages that listen for it directly keep working. onAccept is the handler.
  var ALIAS = { accepted: 'accept' };

  function resolve(target) {
    var el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) throw new Error('DocAI.mount: no element matches ' + target);
    return el;
  }

  function mount(target, opts) {
    opts = opts || {};
    if (!opts.token && !opts.embedUrl) {
      throw new Error('DocAI.mount: pass the token from POST /api/session, or an embedUrl.');
    }

    var host = resolve(target);
    var handlers = {};
    EVENTS.forEach(function (name) {
      var fn = opts['on' + name.charAt(0).toUpperCase() + name.slice(1)];
      handlers[name] = fn ? [fn] : [];
    });

    var iframe = document.createElement('iframe');
    iframe.src = opts.embedUrl || (ORIGIN + '/embed?token=' + encodeURIComponent(opts.token));
    iframe.title = opts.title || 'Document reader';
    iframe.setAttribute('allow', 'clipboard-write');
    iframe.style.cssText = 'width:100%;border:0;display:block;height:' +
      (typeof opts.height === 'number' ? opts.height + 'px' : (opts.height || '660px'));
    if (opts.className) iframe.className = opts.className;
    host.appendChild(iframe);

    var autoResize = opts.autoResize !== false;

    function emit(name, payload) {
      (handlers[name] || []).forEach(function (fn) {
        try { fn(payload); } catch (e) { console.error('DocAI ' + name + ' handler failed', e); }
      });
    }

    function onMessage(e) {
      // Both halves matter: the right origin, and the window we actually made.
      if (e.origin !== ORIGIN) return;
      if (e.source !== iframe.contentWindow) return;
      var m = e.data;
      if (!m || typeof m.type !== 'string' || m.type.indexOf('docai:') !== 0) return;

      var name = m.type.slice(6);
      name = ALIAS[name] || name;
      if (name === 'resize') {
        if (autoResize && m.height > 0) iframe.style.height = Math.ceil(m.height) + 'px';
      }
      if (EVENTS.indexOf(name) !== -1) emit(name, name === 'resize' ? m.height : m);
    }

    window.addEventListener('message', onMessage);

    // Hand a file to the panel, as though it had been dropped on it. The panel
    // is a small part of a host page and a file dropped on the rest of that
    // page is the common gesture; this is how it gets across the iframe
    // boundary. Files are posted as a plain array — a FileList does not
    // reliably survive the clone.
    function read(files) {
      var list = files && typeof files.length === 'number'
        ? Array.prototype.slice.call(files)
        : (files ? [files] : []);
      if (!list.length) return;
      iframe.contentWindow.postMessage({ type: 'docai:read', files: list }, ORIGIN);
    }

    // Opt in with dropTarget: document.body — the element becomes a drop zone
    // that forwards to the panel. Worth having here rather than in every host
    // page, because the half people forget is the preventDefault: the browser's
    // default for a dropped file is to navigate to it, so one miss throws the
    // page away and takes any typed-in form data with it.
    var detachDrop = null;
    if (opts.dropTarget) {
      var zone = resolve(opts.dropTarget);
      var depth = 0;
      var dragging = function (e) {
        var types = (e.dataTransfer || {}).types || [];
        return Array.prototype.indexOf.call(types, 'Files') !== -1;
      };
      var mark = function (on) {
        if (opts.dropClass !== null) zone.classList.toggle(opts.dropClass || 'docai-dropping', on);
      };
      var enter = function (e) { if (dragging(e)) { e.preventDefault(); depth++; mark(true); } };
      var over = function (e) {
        if (!dragging(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
      };
      var leave = function (e) {
        if (!dragging(e)) return;
        depth = Math.max(0, depth - 1);
        if (!depth) mark(false);
      };
      var dropped = function (e) {
        e.preventDefault();
        depth = 0;
        mark(false);
        if (dragging(e)) read(e.dataTransfer.files);
      };
      zone.addEventListener('dragenter', enter);
      zone.addEventListener('dragover', over);
      zone.addEventListener('dragleave', leave);
      zone.addEventListener('drop', dropped);
      detachDrop = function () {
        zone.removeEventListener('dragenter', enter);
        zone.removeEventListener('dragover', over);
        zone.removeEventListener('dragleave', leave);
        zone.removeEventListener('drop', dropped);
      };
    }

    return {
      iframe: iframe,
      origin: ORIGIN,
      read: function (files) { read(files); return this; },
      on: function (name, fn) {
        if (!handlers[name]) handlers[name] = [];
        handlers[name].push(fn);
        return this;
      },
      destroy: function () {
        window.removeEventListener('message', onMessage);
        if (detachDrop) detachDrop();
        if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
      }
    };
  }

  window.DocAI = { mount: mount, origin: ORIGIN, version: '0.1.0' };
})();
