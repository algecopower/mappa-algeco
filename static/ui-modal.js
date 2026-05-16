// UI-MODAL: helper unificato per aprire/chiudere modali.
//
// Aspetta markup con classi documentate in static/mappa.css:
//   <div class="ui-modal" id="myModal" data-no-backdrop-close>
//       <div class="ui-modal-content">
//           <div class="ui-modal-header">
//               <h2 class="ui-modal-title">Titolo</h2>
//               <button class="ui-modal-close" type="button">×</button>
//           </div>
//           <div class="ui-modal-body">...</div>
//       </div>
//   </div>
//
// API:
//   UiModal.open(idOrEl)
//   UiModal.close(idOrEl)
//   UiModal.toggle(idOrEl)
//   UiModal.isOpen(idOrEl)  -> bool
//   UiModal.onClose(idOrEl, callback)  -> registra callback chiusura
//
// Comportamenti automatici (sempre on, opt-out con data-*):
//   - click su .ui-modal-close       → close
//   - click sul backdrop             → close (a meno di data-no-backdrop-close)
//   - tasto ESC                      → close del topmost (a meno di data-no-escape-close)
//   - body.ui-modal-open mentre aperto (per bloccare scroll)

(function () {
    const _openStack = []; // stack di modali aperti (per chiusura ESC del topmost)
    const _onCloseCb = new WeakMap(); // callback registrate

    function _resolve(target) {
        if (typeof target === 'string') return document.getElementById(target);
        return target;
    }

    function _runOnClose(el) {
        const cbs = _onCloseCb.get(el);
        if (cbs) cbs.forEach(cb => { try { cb(el); } catch (e) { console.error(e); } });
    }

    const UiModal = {
        open(target) {
            const el = _resolve(target);
            if (!el || el.classList.contains('visible')) return;
            el.classList.add('visible');
            _openStack.push(el);
            document.body.classList.add('ui-modal-open');
            // Focus sul primo elemento focusable (accessibilità)
            const focusable = el.querySelector('input, textarea, button, select, [tabindex]');
            if (focusable) setTimeout(() => focusable.focus({ preventScroll: true }), 50);
        },

        close(target) {
            const el = _resolve(target);
            if (!el || !el.classList.contains('visible')) return;
            el.classList.remove('visible');
            const idx = _openStack.indexOf(el);
            if (idx !== -1) _openStack.splice(idx, 1);
            if (_openStack.length === 0) {
                document.body.classList.remove('ui-modal-open');
            }
            _runOnClose(el);
        },

        toggle(target) {
            const el = _resolve(target);
            if (!el) return;
            el.classList.contains('visible') ? this.close(el) : this.open(el);
        },

        isOpen(target) {
            const el = _resolve(target);
            return !!(el && el.classList.contains('visible'));
        },

        onClose(target, callback) {
            const el = _resolve(target);
            if (!el) return;
            if (!_onCloseCb.has(el)) _onCloseCb.set(el, []);
            _onCloseCb.get(el).push(callback);
        },
    };

    // Click handlers globali (event delegation: funziona anche per modali
    // aggiunti al DOM dopo il caricamento di questo script).
    document.addEventListener('click', function (e) {
        // X (.ui-modal-close): chiudi il modal genitore
        const closeBtn = e.target.closest('.ui-modal-close');
        if (closeBtn) {
            const modal = closeBtn.closest('.ui-modal');
            if (modal) UiModal.close(modal);
            return;
        }
        // Click sul backdrop (= sull'elemento .ui-modal direttamente, non sui figli)
        if (e.target.classList && e.target.classList.contains('ui-modal')) {
            if (e.target.hasAttribute('data-no-backdrop-close')) return;
            UiModal.close(e.target);
        }
    });

    // ESC chiude il modal in cima allo stack
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || _openStack.length === 0) return;
        const top = _openStack[_openStack.length - 1];
        if (top.hasAttribute('data-no-escape-close')) return;
        UiModal.close(top);
    });

    window.UiModal = UiModal;
})();
