/* ═══════════════════════════════════════════════════════════
   GUIDED TOUR ENGINE
   ═══════════════════════════════════════════════════════════
   Usage:
     AlgecoTour.init({ page: 'mappa', steps: [...] });

   Each step: { target: '#selector', title, icon, body, position? }
   position: 'top'|'bottom'|'left'|'right'|'center' (auto if omitted)
   ═══════════════════════════════════════════════════════════ */

window.AlgecoTour = (function () {
    'use strict';

    let _steps = [];
    let _current = -1;
    let _page = '';
    let _overlay = null;
    let _tooltip = null;
    let _ring = null;
    let _arrow = null;
    let _active = false;
    let _resizeTimer = null;

    /* ── Storage key per utente ── */
    function _storageKey() {
        let user = '';
        try {
            const el = document.querySelector('[data-tour-user]');
            if (el) user = el.getAttribute('data-tour-user');
        } catch (e) {}
        return 'algeco_tour_done_' + (_page || 'default') + (user ? '_' + user : '');
    }

    function _isDone() {
        return localStorage.getItem(_storageKey()) === '1';
    }

    function _markDone() {
        localStorage.setItem(_storageKey(), '1');
    }

    function _resetDone() {
        localStorage.removeItem(_storageKey());
    }

    /* ── Create DOM elements ── */
    function _createOverlay() {
        if (_overlay) _overlay.remove();
        _overlay = document.createElement('div');
        _overlay.className = 'tour-overlay';
        _overlay.innerHTML = '<svg><defs><mask id="tour-mask"><rect width="100%" height="100%" fill="white"/><rect id="tour-hole" rx="8" ry="8" fill="black"/></mask></defs><rect width="100%" height="100%" fill="rgba(0,0,0,0.55)" mask="url(#tour-mask)"/></svg>';
        _overlay.addEventListener('click', function (e) { e.stopPropagation(); });
        document.body.appendChild(_overlay);
    }

    function _createTooltip() {
        if (_tooltip) _tooltip.remove();
        _tooltip = document.createElement('div');
        _tooltip.className = 'tour-tooltip';
        document.body.appendChild(_tooltip);
    }

    function _createRing() {
        if (_ring) _ring.remove();
        _ring = document.createElement('div');
        _ring.className = 'tour-highlight-ring';
        document.body.appendChild(_ring);
    }

    function _createArrow() {
        if (_arrow) _arrow.remove();
        _arrow = document.createElement('div');
        _arrow.className = 'tour-arrow';
        document.body.appendChild(_arrow);
    }

    /* ── Position utilities ── */
    function _getRect(el) {
        var r = el.getBoundingClientRect();
        return { top: r.top, left: r.left, width: r.width, height: r.height, bottom: r.bottom, right: r.right };
    }

    function _bestPosition(rect) {
        var vw = window.innerWidth, vh = window.innerHeight;
        var spaceTop = rect.top, spaceBottom = vh - rect.bottom;
        var spaceLeft = rect.left, spaceRight = vw - rect.right;

        // Minimum usable space for the tooltip (with padding)
        var minW = 280, minH = 150;

        // Score each position: prefer the one with the most room on BOTH axes
        var candidates = [
            { pos: 'bottom', primary: spaceBottom, cross: Math.min(spaceLeft + rect.width / 2, spaceRight + rect.width / 2, vw) },
            { pos: 'top',    primary: spaceTop,    cross: Math.min(spaceLeft + rect.width / 2, spaceRight + rect.width / 2, vw) },
            { pos: 'right',  primary: spaceRight,  cross: Math.min(spaceTop + rect.height / 2, spaceBottom + rect.height / 2, vh) },
            { pos: 'left',   primary: spaceLeft,   cross: Math.min(spaceTop + rect.height / 2, spaceBottom + rect.height / 2, vh) }
        ];

        // Filter out positions where the tooltip simply cannot fit
        var viable = candidates.filter(function (c) {
            if (c.pos === 'left' || c.pos === 'right') {
                return c.primary >= minW && c.cross >= minH;
            }
            return c.primary >= minH && c.cross >= minW;
        });

        if (viable.length === 0) viable = candidates; // fallback to all

        viable.sort(function (a, b) { return b.primary - a.primary; });
        return viable[0].pos;
    }

    function _positionTooltip(rect, pos) {
        var pad = 18;
        var vw = window.innerWidth, vh = window.innerHeight;

        // Dynamically constrain max-width based on available space
        var maxW = 380;
        if (pos === 'right') {
            maxW = Math.min(380, vw - rect.right - pad * 2 - 12);
        } else if (pos === 'left') {
            maxW = Math.min(380, rect.left - pad * 2 - 12);
        }
        maxW = Math.max(240, maxW); // absolute minimum
        _tooltip.style.maxWidth = maxW + 'px';

        // Force layout recalc after maxWidth change
        var tw = _tooltip.offsetWidth, th = _tooltip.offsetHeight;
        var top, left;

        switch (pos) {
            case 'bottom':
                top = rect.bottom + pad;
                left = rect.left + rect.width / 2 - tw / 2;
                break;
            case 'top':
                top = rect.top - th - pad;
                left = rect.left + rect.width / 2 - tw / 2;
                break;
            case 'right':
                top = rect.top + rect.height / 2 - th / 2;
                left = rect.right + pad;
                break;
            case 'left':
                top = rect.top + rect.height / 2 - th / 2;
                left = rect.left - tw - pad;
                break;
            case 'center':
                top = vh / 2 - th / 2;
                left = vw / 2 - tw / 2;
                break;
        }

        // Clamp to viewport with generous margin
        left = Math.max(10, Math.min(left, vw - tw - 10));
        top = Math.max(10, Math.min(top, vh - th - 10));

        // Re-read after clamp to check overlap with target
        if (pos !== 'center' && rect) {
            var tRect = { top: top, left: left, right: left + tw, bottom: top + th };
            var overlap = !(tRect.right < rect.left || tRect.left > rect.right || tRect.bottom < rect.top || tRect.top > rect.bottom);
            if (overlap) {
                // Renegotiate: pick the best alternative axis
                var spaceBelow = vh - rect.bottom - pad;
                var spaceAbove = rect.top - pad;
                var spaceRight = vw - rect.right - pad;
                var spaceLeftSide = rect.left - pad;

                if (pos === 'bottom' || pos === 'top') {
                    if (spaceRight >= tw + pad) { left = rect.right + pad; }
                    else if (spaceLeftSide >= tw + pad) { left = rect.left - tw - pad; }
                    else { top = rect.bottom + pad; } // push below
                } else {
                    if (spaceBelow >= th + pad) { top = rect.bottom + pad; }
                    else if (spaceAbove >= th + pad) { top = rect.top - th - pad; }
                }
                // Re-clamp
                left = Math.max(10, Math.min(left, vw - tw - 10));
                top = Math.max(10, Math.min(top, vh - th - 10));
            }
        }

        _tooltip.style.top = top + 'px';
        _tooltip.style.left = left + 'px';
    }

    /* ── Draw arrow SVG ── */
    function _drawArrow(rect, pos) {
        if (pos === 'center' || !rect) {
            _arrow.innerHTML = '';
            return;
        }
        var tw = _tooltip.offsetWidth, th = _tooltip.offsetHeight;
        var ttop = parseFloat(_tooltip.style.top), tleft = parseFloat(_tooltip.style.left);

        // Don't draw arrow if tooltip overlaps the target
        var tipRect = { top: ttop, left: tleft, right: tleft + tw, bottom: ttop + th };
        var overlap = !(tipRect.right < rect.left || tipRect.left > rect.right ||
                        tipRect.bottom < rect.top || tipRect.top > rect.bottom);
        if (overlap) {
            _arrow.innerHTML = '';
            return;
        }

        // Find the closest edge point on tooltip facing the target
        var targetCX = rect.left + rect.width / 2;
        var targetCY = rect.top + rect.height / 2;
        var tipCX = tleft + tw / 2;
        var tipCY = ttop + th / 2;

        var startX, startY, endX, endY;

        // Start: point on tooltip edge closest to target center
        var angle = Math.atan2(targetCY - tipCY, targetCX - tipCX);
        // Intersect with tooltip rectangle
        var hw = tw / 2, hh = th / 2;
        var tanA = Math.abs(Math.tan(angle));
        if (tanA * hw <= hh) {
            // hits left or right edge
            startX = tipCX + (Math.cos(angle) > 0 ? hw : -hw);
            startY = tipCY + (Math.cos(angle) > 0 ? hw : -hw) * Math.tan(angle);
        } else {
            // hits top or bottom edge
            startY = tipCY + (Math.sin(angle) > 0 ? hh : -hh);
            startX = tipCX + (Math.sin(angle) > 0 ? hh : -hh) / Math.tan(angle);
        }

        // End: point on target edge closest to tooltip center
        var rCX = rect.left + rect.width / 2, rCY = rect.top + rect.height / 2;
        var angle2 = Math.atan2(tipCY - rCY, tipCX - rCX);
        var rhw = rect.width / 2, rhh = rect.height / 2;
        var tanA2 = Math.abs(Math.tan(angle2));
        if (rhw > 0 && tanA2 * rhw <= rhh) {
            endX = rCX + (Math.cos(angle2) > 0 ? rhw : -rhw);
            endY = rCY + (Math.cos(angle2) > 0 ? rhw : -rhw) * Math.tan(angle2);
        } else if (rhh > 0) {
            endY = rCY + (Math.sin(angle2) > 0 ? rhh : -rhh);
            endX = rCX + (Math.sin(angle2) > 0 ? rhh : -rhh) / Math.tan(angle2);
        } else {
            endX = rCX; endY = rCY;
        }

        // Clamp to viewport
        startX = Math.max(2, Math.min(startX, window.innerWidth - 2));
        startY = Math.max(2, Math.min(startY, window.innerHeight - 2));
        endX = Math.max(2, Math.min(endX, window.innerWidth - 2));
        endY = Math.max(2, Math.min(endY, window.innerHeight - 2));

        var dx = endX - startX, dy = endY - startY;
        var len = Math.sqrt(dx * dx + dy * dy);
        if (len < 15) { _arrow.innerHTML = ''; return; }

        // Arrowhead
        var aAngle = Math.atan2(dy, dx);
        var aLen = 10;
        var ax1 = endX - aLen * Math.cos(aAngle - 0.35);
        var ay1 = endY - aLen * Math.sin(aAngle - 0.35);
        var ax2 = endX - aLen * Math.cos(aAngle + 0.35);
        var ay2 = endY - aLen * Math.sin(aAngle + 0.35);

        // Subtle curve control point (perpendicular offset)
        var curveFactor = Math.min(0.12, 30 / len);
        var cx = (startX + endX) / 2 + dy * curveFactor;
        var cy = (startY + endY) / 2 - dx * curveFactor;

        var minX = Math.min(startX, endX, cx, ax1, ax2) - 5;
        var minY = Math.min(startY, endY, cy, ay1, ay2) - 5;
        var maxX = Math.max(startX, endX, cx, ax1, ax2) + 5;
        var maxY = Math.max(startY, endY, cy, ay1, ay2) + 5;

        _arrow.style.left = minX + 'px';
        _arrow.style.top = minY + 'px';
        _arrow.style.width = (maxX - minX) + 'px';
        _arrow.style.height = (maxY - minY) + 'px';

        var ox = -minX, oy = -minY;
        _arrow.innerHTML = '<svg width="' + (maxX - minX) + '" height="' + (maxY - minY) + '" style="overflow:visible">' +
            '<path d="M' + (startX + ox) + ',' + (startY + oy) + ' Q' + (cx + ox) + ',' + (cy + oy) + ' ' + (endX + ox) + ',' + (endY + oy) + '"/>' +
            '<polygon points="' + (endX + ox) + ',' + (endY + oy) + ' ' + (ax1 + ox) + ',' + (ay1 + oy) + ' ' + (ax2 + ox) + ',' + (ay2 + oy) + '"/>' +
            '</svg>';
    }

    /* ── Spotlight hole ── */
    function _setSpotlight(rect) {
        var hole = document.getElementById('tour-hole');
        if (!hole) return;
        if (!rect) {
            hole.setAttribute('width', 0);
            hole.setAttribute('height', 0);
            return;
        }
        var pad = 6;
        hole.setAttribute('x', rect.left - pad);
        hole.setAttribute('y', rect.top - pad);
        hole.setAttribute('width', rect.width + pad * 2);
        hole.setAttribute('height', rect.height + pad * 2);
    }

    /* ── Highlight ring ── */
    function _setRing(rect) {
        if (!rect) { _ring.style.display = 'none'; return; }
        _ring.style.display = 'block';
        var pad = 6;
        _ring.style.top = (rect.top - pad) + 'px';
        _ring.style.left = (rect.left - pad) + 'px';
        _ring.style.width = (rect.width + pad * 2) + 'px';
        _ring.style.height = (rect.height + pad * 2) + 'px';
    }

    /* ── Render step dots ── */
    function _renderDots() {
        var html = '<div class="tour-step-indicator">';
        for (var i = 0; i < _steps.length; i++) {
            var cls = i === _current ? 'active' : (i < _current ? 'done' : '');
            html += '<div class="tour-step-dot ' + cls + '"></div>';
        }
        html += '<span class="tour-step-count">' + (_current + 1) + ' / ' + _steps.length + '</span></div>';
        return html;
    }

    /* ── Elevated elements tracker ── */
    var _elevated = [];

    function _elevate(selector) {
        var el = typeof selector === 'string' ? document.querySelector(selector) : selector;
        if (!el) return;
        el._tourOldZIndex = el.style.zIndex;
        el._tourOldPosition = el.style.position;
        el.style.zIndex = '100001';
        if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
        _elevated.push(el);
    }

    function _restoreAll() {
        _elevated.forEach(function (el) {
            if (el._tourOldZIndex !== undefined) el.style.zIndex = el._tourOldZIndex;
            if (el._tourOldPosition !== undefined) el.style.position = el._tourOldPosition;
        });
        _elevated = [];
    }

    /* ── Show a step ── */
    function _showStep(idx) {
        if (idx < 0 || idx >= _steps.length) return;

        // Leave previous step
        if (_current >= 0 && _current < _steps.length) {
            var prev = _steps[_current];
            if (prev.onLeave) { try { prev.onLeave(); } catch(e) {} }
        }
        _restoreAll();

        _current = idx;
        var step = _steps[idx];

        // Enter new step
        if (step.onEnter) { try { step.onEnter({ elevate: _elevate }); } catch(e) {} }

        // Small delay for DOM changes from onEnter
        var delay = step.onEnter ? (step.enterDelay || 250) : 0;
        setTimeout(function () { _renderStepVisuals(step); }, delay);
    }

    function _renderStepVisuals(step) {
        // Find target element
        var targetEl = null;
        var rect = null;
        if (step.target && step.target !== 'none') {
            targetEl = document.querySelector(step.target);
            if (targetEl) {
                // Scroll into view if needed
                targetEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                rect = _getRect(targetEl);
            }
        }

        var pos = step.position || (rect ? _bestPosition(rect) : 'center');

        // Validate requested position: override if there's clearly not enough space
        if (rect && pos !== 'center') {
            var vw = window.innerWidth, vh = window.innerHeight;
            var minNeeded = 280; // minimum tooltip width/height needed
            var ok = true;
            if (pos === 'right' && (vw - rect.right) < minNeeded + 40) ok = false;
            if (pos === 'left' && rect.left < minNeeded + 40) ok = false;
            if (pos === 'bottom' && (vh - rect.bottom) < 160) ok = false;
            if (pos === 'top' && rect.top < 160) ok = false;
            if (!ok) pos = _bestPosition(rect);
        }

        // Update spotlight
        _setSpotlight(rect);
        _setRing(rect);

        // Render tooltip content
        var isFirst = _current === 0;
        var isLast = _current === _steps.length - 1;

        var html = _renderDots();
        html += '<div class="tour-title">';
        if (step.icon) html += '<span class="tour-icon">' + step.icon + '</span>';
        html += step.title + '</div>';
        html += '<div class="tour-body">' + step.body + '</div>';
        html += '<div class="tour-buttons">';
        html += '<button class="tour-btn tour-btn-skip" onclick="AlgecoTour.end()">Salta guida</button>';
        if (!isFirst) html += '<button class="tour-btn tour-btn-prev" onclick="AlgecoTour.prev()">← Indietro</button>';
        if (isLast) {
            html += '<button class="tour-btn tour-btn-next" onclick="AlgecoTour.end(true)">✓ Fine</button>';
        } else {
            html += '<button class="tour-btn tour-btn-next" onclick="AlgecoTour.next()">Avanti →</button>';
        }
        html += '</div>';

        _tooltip.innerHTML = html;
        _tooltip.classList.remove('visible');

        // Position (need a frame for dimensions)
        requestAnimationFrame(function () {
            _positionTooltip(rect || { top: 0, left: 0, width: window.innerWidth, height: window.innerHeight, bottom: window.innerHeight, right: window.innerWidth }, pos);
            _drawArrow(rect, pos);
            requestAnimationFrame(function () { _tooltip.classList.add('visible'); });
        });
    }

    /* ── Welcome dialog ── */
    function _showWelcome(onStart, onSkip) {
        var el = document.createElement('div');
        el.className = 'tour-welcome-overlay';
        el.innerHTML = '<div class="tour-welcome-card">' +
            '<div class="tour-welcome-emoji">👋</div>' +
            '<h2>Benvenuto in Mappa Algeco!</h2>' +
            '<p>Vuoi fare un rapido tour guidato per scoprire le funzionalità principali di questa pagina?<br>Ci vorranno meno di 2 minuti.</p>' +
            '<div class="tour-welcome-buttons">' +
            '<button class="tour-btn tour-btn-prev" id="tour-welcome-skip">No, grazie</button>' +
            '<button class="tour-btn tour-btn-next" id="tour-welcome-start">🚀 Inizia il tour</button>' +
            '</div></div>';
        document.body.appendChild(el);

        document.getElementById('tour-welcome-start').onclick = function () {
            el.style.opacity = '0';
            setTimeout(function () { el.remove(); onStart(); }, 300);
        };
        document.getElementById('tour-welcome-skip').onclick = function () {
            el.style.opacity = '0';
            setTimeout(function () { el.remove(); onSkip(); }, 300);
        };
    }

    /* ── Window resize handler ── */
    function _onResize() {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(function () {
            if (_active && _current >= 0) _showStep(_current);
        }, 150);
    }

    /* ── Keyboard handler ── */
    function _onKey(e) {
        if (!_active) return;
        if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); AlgecoTour.next(); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); AlgecoTour.prev(); }
        else if (e.key === 'Escape') { e.preventDefault(); AlgecoTour.end(); }
    }

    /* ═══════════ PUBLIC API ═══════════ */

    var _initTimer = null;

    function init(opts) {
        // Cancel any pending init from a previous tour
        if (_initTimer) { clearTimeout(_initTimer); _initTimer = null; }
        // End any active tour silently
        if (_active) { _endSilent(); }

        _page = opts.page || '';
        _steps = opts.steps || [];

        // Wait for DOM + initial data load
        var delay = opts.delay || 1500;

        _initTimer = setTimeout(function () {
            _initTimer = null;
            if (!_isDone()) {
                _showWelcome(
                    function () { _startTour(); },
                    function () { _markDone(); }
                );
            }
        }, delay);
    }

    function _endSilent() {
        if (_current >= 0 && _current < _steps.length) {
            var step = _steps[_current];
            if (step.onLeave) { try { step.onLeave(); } catch(e) {} }
        }
        _restoreAll();
        _active = false;
        _current = -1;
        window.removeEventListener('resize', _onResize);
        window.removeEventListener('keydown', _onKey);
        if (_overlay) { _overlay.remove(); _overlay = null; }
        if (_tooltip) { _tooltip.remove(); _tooltip = null; }
        if (_ring) { _ring.remove(); _ring = null; }
        if (_arrow) { _arrow.remove(); _arrow = null; }
    }

    function _startTour() {
        if (_active) _endSilent();
        _active = true;
        _createOverlay();
        _createTooltip();
        _createRing();
        _createArrow();
        window.addEventListener('resize', _onResize);
        window.addEventListener('keydown', _onKey);
        _showStep(0);
    }

    function start(force) {
        if (force) _resetDone();
        _startTour();
    }

    function next() {
        if (_current < _steps.length - 1) _showStep(_current + 1);
        else end(true);
    }

    function prev() {
        if (_current > 0) _showStep(_current - 1);
    }

    function end(completed) {
        // Leave current step
        if (_current >= 0 && _current < _steps.length) {
            var step = _steps[_current];
            if (step.onLeave) { try { step.onLeave(); } catch(e) {} }
        }
        _restoreAll();

        _active = false;
        _current = -1;
        window.removeEventListener('resize', _onResize);
        window.removeEventListener('keydown', _onKey);

        // Fade out
        if (_overlay) { _overlay.classList.add('fade-out'); }
        if (_tooltip) { _tooltip.classList.remove('visible'); }
        if (_ring) { _ring.style.display = 'none'; }
        if (_arrow) { _arrow.innerHTML = ''; }

        setTimeout(function () {
            if (_overlay) { _overlay.remove(); _overlay = null; }
            if (_tooltip) { _tooltip.remove(); _tooltip = null; }
            if (_ring) { _ring.remove(); _ring = null; }
            if (_arrow) { _arrow.remove(); _arrow = null; }
        }, 350);

        _markDone();
    }

    return { init: init, start: start, next: next, prev: prev, end: end };
})();
