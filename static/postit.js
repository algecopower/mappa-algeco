/**
 * Post-it — Sistema promemoria sempre visibile.
 * Si auto-inietta CSS + HTML al caricamento.
 */
(function() {
'use strict';

/* ===== CSS ===== */
var CSS = `
/* Post-it FAB */
.postit-fab {
    position: fixed;
    bottom: 70px;
    right: 12px;
    width: 48px;
    height: 48px;
    background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
    color: white;
    border: none;
    border-radius: 50%;
    cursor: pointer;
    font-size: 20px;
    z-index: 9998;
    box-shadow: 0 4px 15px rgba(245,158,11,0.45);
    transition: all .3s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    line-height: 1;
}
[data-theme="dark"] .postit-fab {
    background: linear-gradient(135deg, #f59e0b 0%, #b45309 100%);
}
.postit-fab:hover {
    transform: scale(1.1);
    box-shadow: 0 6px 22px rgba(245,158,11,0.6);
}
.postit-fab:active { transform: scale(.95); }
.postit-fab .postit-badge {
    position: absolute;
    top: -4px; right: -4px;
    background: #ef4444;
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    min-width: 20px;
    height: 20px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 5px;
    box-shadow: 0 2px 6px rgba(0,0,0,.3);
}
.postit-badge.hidden { display: none; }

/* Panel */
.postit-panel {
    position: fixed;
    bottom: 128px;
    right: 12px;
    width: 380px;
    max-height: 520px;
    background: var(--bg-secondary, #1e293b);
    border: 1px solid var(--border, #334155);
    border-radius: 16px;
    box-shadow: 0 12px 40px var(--shadow, rgba(0,0,0,.3));
    z-index: 9998;
    transform: translateY(20px);
    opacity: 0;
    pointer-events: none;
    transition: all .3s cubic-bezier(.4,0,.2,1);
    overflow: hidden;
    display: flex;
    flex-direction: column;
}
.postit-panel.visible {
    transform: translateY(0);
    opacity: 1;
    pointer-events: all;
}

/* Panel header */
.postit-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border, #334155);
    background: var(--bg-tertiary, #0f172a);
}
.postit-panel-title {
    font-weight: 700;
    font-size: .95em;
    color: var(--text-primary, #eee);
}
.postit-panel-actions {
    display: flex;
    gap: 6px;
}
.postit-panel-actions button, .postit-panel-actions a {
    background: none;
    border: none;
    color: var(--text-secondary, #94a3b8);
    cursor: pointer;
    font-size: 16px;
    padding: 4px 6px;
    border-radius: 6px;
    transition: all .2s;
    text-decoration: none;
    display: flex;
    align-items: center;
}
.postit-panel-actions button:hover, .postit-panel-actions a:hover {
    background: var(--border, #334155);
    color: var(--text-primary, #eee);
}

/* Notes list */
.postit-list {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.postit-list::-webkit-scrollbar { width: 5px; }
.postit-list::-webkit-scrollbar-thumb { background: var(--border, #334155); border-radius: 4px; }

.postit-empty {
    text-align: center;
    padding: 30px 20px;
    color: var(--text-muted, #64748b);
    font-size: .9em;
}

/* Single note card */
.postit-card {
    border-radius: 10px;
    padding: 10px 12px;
    position: relative;
    transition: all .2s;
    border-left: 4px solid transparent;
    cursor: default;
}
.postit-card:hover {
    box-shadow: 0 2px 12px rgba(0,0,0,.15);
}
.postit-card[data-priorita="alta"] { border-left-color: #ef4444; }
.postit-card[data-priorita="media"] { border-left-color: #f59e0b; }
.postit-card[data-priorita="bassa"] { border-left-color: #22c55e; }

.postit-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
}
.postit-card-cat {
    font-size: .75em;
    font-weight: 600;
    opacity: .8;
    display: flex;
    align-items: center;
    gap: 4px;
}
.postit-card-actions {
    display: flex;
    gap: 2px;
}
.postit-card-actions button {
    background: none;
    border: none;
    font-size: 13px;
    cursor: pointer;
    padding: 2px 4px;
    border-radius: 4px;
    opacity: .5;
    transition: opacity .2s;
}
.postit-card-actions button:hover { opacity: 1; }

.postit-card-text {
    font-size: .88em;
    line-height: 1.4;
    color: #1e293b;
    word-break: break-word;
    white-space: pre-wrap;
}
.postit-card-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 6px;
    font-size: .7em;
    opacity: .6;
    color: #475569;
}
.postit-card-scadenza {
    color: #ef4444;
    font-weight: 600;
    opacity: 1;
}

/* Form (create/edit) */
.postit-form {
    padding: 12px;
    border-top: 1px solid var(--border, #334155);
    background: var(--bg-tertiary, #0f172a);
    display: none;
    flex-direction: column;
    gap: 8px;
}
.postit-form.active { display: flex; }

.postit-form textarea {
    width: 100%;
    border: 1px solid var(--border, #334155);
    border-radius: 8px;
    padding: 8px 10px;
    font-size: .88em;
    font-family: inherit;
    resize: vertical;
    min-height: 60px;
    max-height: 120px;
    background: var(--bg-secondary, #1e293b);
    color: var(--text-primary, #eee);
    outline: none;
    transition: border-color .2s;
}
.postit-form textarea:focus {
    border-color: #f59e0b;
}

.postit-form-row {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
}

.postit-form select, .postit-form input[type="date"] {
    flex: 1;
    min-width: 0;
    padding: 6px 8px;
    border: 1px solid var(--border, #334155);
    border-radius: 6px;
    font-size: .82em;
    font-family: inherit;
    background: var(--bg-secondary, #1e293b);
    color: var(--text-primary, #eee);
    outline: none;
}
.postit-form select:focus, .postit-form input[type="date"]:focus {
    border-color: #f59e0b;
}

/* Color picker dots */
.postit-colori {
    display: flex;
    gap: 4px;
    align-items: center;
}
.postit-colore-dot {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    border: 2px solid transparent;
    cursor: pointer;
    transition: all .2s;
}
.postit-colore-dot:hover { transform: scale(1.15); }
.postit-colore-dot.selected { border-color: #f59e0b; box-shadow: 0 0 0 2px rgba(245,158,11,.3); }

.postit-form-btns {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
}
.postit-form-btns button {
    padding: 6px 14px;
    border: none;
    border-radius: 6px;
    font-size: .82em;
    font-weight: 600;
    cursor: pointer;
    transition: all .2s;
}
.postit-btn-salva {
    background: #f59e0b;
    color: #1e293b;
}
.postit-btn-salva:hover { background: #d97706; }
.postit-btn-annulla {
    background: var(--border, #334155);
    color: var(--text-primary, #eee);
}
.postit-btn-annulla:hover { opacity: .8; }

/* Strip promemoria in alto */
.postit-strip {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 9990;
    display: flex;
    flex-wrap: wrap;
    gap: 0;
    pointer-events: none;
    transition: all .3s;
}
.postit-strip.hidden { display: none; }
.postit-strip-item {
    flex: 1;
    min-width: 200px;
    padding: 6px 14px;
    font-size: .8em;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 6px;
    pointer-events: auto;
    cursor: pointer;
    transition: opacity .2s;
    justify-content: center;
    text-align: center;
    color: #1e293b;
    border-bottom: 2px solid rgba(0,0,0,.1);
}
.postit-strip-item:hover { opacity: .85; }
.postit-strip-item[data-priorita="alta"] {
    animation: postit-pulse 2s infinite;
}
@keyframes postit-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: .7; }
}

/* Responsive */
@media (max-width: 600px) {
    .postit-panel { width: calc(100vw - 20px); right: 10px; bottom: 120px; }
    .postit-strip-item { font-size: .72em; padding: 4px 8px; }
    .postit-cal-modal-body { max-height: 50vh; }
}

/* Calendario import modal */
.postit-cal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,.5);
    backdrop-filter: blur(4px);
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity .25s;
}
.postit-cal-overlay.visible {
    opacity: 1;
    pointer-events: all;
}
.postit-cal-modal {
    background: var(--bg-secondary, #1e293b);
    border: 1px solid var(--border, #334155);
    border-radius: 14px;
    width: 400px;
    max-width: 92vw;
    box-shadow: 0 16px 50px rgba(0,0,0,.4);
    overflow: hidden;
}
.postit-cal-modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border, #334155);
    background: var(--bg-tertiary, #0f172a);
}
.postit-cal-modal-header span {
    font-weight: 700;
    font-size: .95em;
    color: var(--text-primary, #eee);
}
.postit-cal-modal-header button {
    background: none;
    border: none;
    color: var(--text-secondary, #94a3b8);
    font-size: 16px;
    cursor: pointer;
    padding: 4px 6px;
    border-radius: 6px;
}
.postit-cal-modal-header button:hover { background: var(--border, #334155); color: var(--text-primary, #eee); }
.postit-cal-modal-body {
    padding: 10px;
    max-height: 350px;
    overflow-y: auto;
}
.postit-cal-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 8px;
    cursor: pointer;
    transition: background .15s;
    border-bottom: 1px solid var(--border, #334155);
}
.postit-cal-item:last-child { border-bottom: none; }
.postit-cal-item:hover { background: var(--border, #334155); }
.postit-cal-item-info {
    flex: 1;
    min-width: 0;
}
.postit-cal-item-title {
    font-size: .85em;
    font-weight: 600;
    color: var(--text-primary, #eee);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.postit-cal-item-meta {
    font-size: .73em;
    color: var(--text-secondary, #94a3b8);
    margin-top: 2px;
}
.postit-cal-item-btn {
    background: #f59e0b;
    color: #1e293b;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: .78em;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    transition: background .2s;
}
.postit-cal-item-btn:hover { background: #d97706; }
.postit-cal-empty {
    text-align: center;
    padding: 24px;
    color: var(--text-muted, #64748b);
    font-size: .85em;
}
.postit-cal-footer {
    padding: 10px 16px;
    border-top: 1px solid var(--border, #334155);
    text-align: center;
}
.postit-cal-footer a {
    color: #f59e0b;
    font-size: .82em;
    font-weight: 600;
    text-decoration: none;
}
.postit-cal-footer a:hover { text-decoration: underline; }
`;

/* ===== STATE ===== */
var notes = [];
var panelOpen = false;
var editingId = null;
var META = { categorie: {}, colori: {} };

/* ===== COLORI POSTIT (testo scuro) ===== */
var COLORI = {
    giallo:  '#fef3c7',
    rosa:    '#fce7f3',
    verde:   '#d1fae5',
    azzurro: '#dbeafe',
    arancio: '#ffedd5',
    viola:   '#ede9fe'
};
var CATEGORIE = {
    assenza:    { label: 'Assenza operatore', icona: '🚫' },
    scadenza:   { label: 'Scadenza',          icona: '⏰' },
    attivita:   { label: 'Attività',           icona: '📋' },
    importante: { label: 'Importante',         icona: '⚠️' },
    generico:   { label: 'Generico',           icona: '📌' }
};

/* ===== INJECT CSS ===== */
function injectCSS() {
    var s = document.createElement('style');
    s.textContent = CSS;
    document.head.appendChild(s);
}

/* ===== INJECT HTML ===== */
function injectHTML() {
    // FAB
    var fab = document.createElement('button');
    fab.className = 'postit-fab';
    fab.id = 'postitFab';
    fab.title = 'Post-it Promemoria';
    fab.innerHTML = '📝<span class="postit-badge hidden" id="postitBadge">0</span>';
    fab.onclick = togglePanel;
    document.body.appendChild(fab);

    // Panel
    var panel = document.createElement('div');
    panel.className = 'postit-panel';
    panel.id = 'postitPanel';
    panel.innerHTML = [
        '<div class="postit-panel-header">',
        '  <div class="postit-panel-title">📝 Post-it Promemoria</div>',
        '  <div class="postit-panel-actions">',
        '    <button onclick="window._postit.nuovo()" title="Nuovo post-it">➕</button>',
        '    <button onclick="window._postit.apriImportCalendario()" title="Importa dal calendario">⬇️</button>',
        '    <button onclick="window._postit.toggleStrip()" title="Mostra/nascondi barra in alto" id="postitStripToggle">📌</button>',
        '    <a href="/calendario" target="_blank" title="Apri Calendario">📅</a>',
        '    <button onclick="window._postit.toggle()" title="Chiudi">✕</button>',
        '  </div>',
        '</div>',
        '<div class="postit-list" id="postitList">',
        '  <div class="postit-empty">⏳ Caricamento...</div>',
        '</div>',
        '<div class="postit-form" id="postitForm">',
        '  <textarea id="postitTesto" placeholder="Scrivi il promemoria..." maxlength="500"></textarea>',
        '  <div class="postit-form-row">',
        '    <select id="postitCategoria">',
             Object.keys(CATEGORIE).map(function(k) {
                 return '<option value="' + k + '">' + CATEGORIE[k].icona + ' ' + CATEGORIE[k].label + '</option>';
             }).join(''),
        '    </select>',
        '    <select id="postitPriorita">',
        '      <option value="alta">🔴 Alta</option>',
        '      <option value="media" selected>🟡 Media</option>',
        '      <option value="bassa">🟢 Bassa</option>',
        '    </select>',
        '  </div>',
        '  <div class="postit-form-row">',
        '    <div class="postit-colori" id="postitColori">',
             Object.keys(COLORI).map(function(k) {
                 return '<div class="postit-colore-dot' + (k === 'giallo' ? ' selected' : '') + '" data-colore="' + k + '" style="background:' + COLORI[k] + '" title="' + k + '"></div>';
             }).join(''),
        '    </div>',
        '    <input type="date" id="postitScadenza" title="Scadenza (opzionale)" style="max-width:140px">',
        '  </div>',
        '  <div class="postit-form-btns">',
        '    <button class="postit-btn-annulla" onclick="window._postit.annullaForm()">Annulla</button>',
        '    <button class="postit-btn-salva" onclick="window._postit.salva()">Salva</button>',
        '  </div>',
        '</div>'
    ].join('\n');
    document.body.appendChild(panel);

    // Strip in alto
    var strip = document.createElement('div');
    strip.className = 'postit-strip';
    strip.id = 'postitStrip';
    document.body.appendChild(strip);

    // Modal importa da calendario
    var overlay = document.createElement('div');
    overlay.className = 'postit-cal-overlay';
    overlay.id = 'postitCalOverlay';
    overlay.innerHTML = [
        '<div class="postit-cal-modal">',
        '  <div class="postit-cal-modal-header">',
        '    <span>📅 Importa dal Calendario</span>',
        '    <button onclick="window._postit.chiudiImportCalendario()">✕</button>',
        '  </div>',
        '  <div class="postit-cal-modal-body" id="postitCalLista">',
        '    <div class="postit-cal-empty">⏳ Caricamento...</div>',
        '  </div>',
        '  <div class="postit-cal-footer">',
        '    <a href="/calendario" target="_blank">🗓️ Apri Calendario completo</a>',
        '  </div>',
        '</div>'
    ].join('\n');
    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) chiudiImportCalendario();
    });
    document.body.appendChild(overlay);

    // Color picker click
    panel.querySelectorAll('.postit-colore-dot').forEach(function(dot) {
        dot.onclick = function() {
            panel.querySelectorAll('.postit-colore-dot').forEach(function(d) { d.classList.remove('selected'); });
            dot.classList.add('selected');
        };
    });
}

/* ===== API ===== */
function api(method, url, body) {
    var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function(r) { return r.json(); });
}

function carica() {
    return api('GET', '/api/postit').then(function(res) {
        if (res.ok) {
            notes = res.notes || [];
            render();
            renderStrip();
            updateBadge();
        }
    }).catch(function(e) {
        console.error('[PostIt] Errore caricamento:', e);
    });
}

/* ===== RENDER ===== */
function render() {
    var list = document.getElementById('postitList');
    if (!list) return;

    if (notes.length === 0) {
        list.innerHTML = '<div class="postit-empty">📌 Nessun promemoria.<br><small>Clicca ➕ per aggiungerne uno</small></div>';
        return;
    }

    list.innerHTML = notes.map(function(n) {
        var cat = CATEGORIE[n.categoria] || CATEGORIE.generico;
        var bg = COLORI[n.colore] || COLORI.giallo;
        var data = n.creato_il ? new Date(n.creato_il).toLocaleDateString('it-IT', { day: '2-digit', month: 'short' }) : '';
        var scad = '';
        if (n.scadenza) {
            var oggi = new Date(); oggi.setHours(0,0,0,0);
            var ds = new Date(n.scadenza + 'T00:00:00'); 
            var diff = Math.ceil((ds - oggi) / 86400000);
            if (diff < 0) scad = '⚠️ Scaduto!';
            else if (diff === 0) scad = '⏰ Oggi!';
            else if (diff === 1) scad = '⏰ Domani';
            else scad = '📅 ' + ds.toLocaleDateString('it-IT', { day: '2-digit', month: 'short' });
        }
        return [
            '<div class="postit-card" data-priorita="' + (n.priorita || 'media') + '" data-id="' + n.id + '" style="background:' + bg + '">',
            '  <div class="postit-card-header">',
            '    <span class="postit-card-cat">' + cat.icona + ' ' + cat.label + '</span>',
            '    <div class="postit-card-actions">',
            '      <button onclick="window._postit.syncCalendario(\'' + n.id + '\', this)" title="Aggiungi al calendario">⬆️</button>',
            '      <button onclick="window._postit.modifica(\'' + n.id + '\')" title="Modifica">✏️</button>',
            '      <button onclick="window._postit.elimina(\'' + n.id + '\')" title="Elimina">🗑️</button>',
            '    </div>',
            '  </div>',
            '  <div class="postit-card-text">' + escHtml(n.testo) + '</div>',
            '  <div class="postit-card-footer">',
            '    <span>' + (n.creato_da || '') + ' · ' + data + '</span>',
            '    <span class="' + (scad.includes('Scaduto') || scad.includes('Oggi') ? 'postit-card-scadenza' : '') + '">' + scad + '</span>',
            '  </div>',
            '</div>'
        ].join('');
    }).join('');
}

function renderStrip() {
    var strip = document.getElementById('postitStrip');
    if (!strip) return;

    // Mostra solo note "alta" priorità + scadenze prossime nella strip
    var rilevanti = notes.filter(function(n) {
        if (n.priorita === 'alta') return true;
        if (n.scadenza) {
            var oggi = new Date(); oggi.setHours(0,0,0,0);
            var ds = new Date(n.scadenza + 'T00:00:00');
            var diff = Math.ceil((ds - oggi) / 86400000);
            if (diff <= 2) return true;
        }
        return false;
    });

    if (rilevanti.length === 0) {
        strip.innerHTML = '';
        return;
    }

    strip.innerHTML = rilevanti.map(function(n) {
        var cat = CATEGORIE[n.categoria] || CATEGORIE.generico;
        var bg = COLORI[n.colore] || COLORI.giallo;
        var scadTxt = '';
        if (n.scadenza) {
            var oggi = new Date(); oggi.setHours(0,0,0,0);
            var ds = new Date(n.scadenza + 'T00:00:00');
            var diff = Math.ceil((ds - oggi) / 86400000);
            if (diff < 0) scadTxt = ' — ⚠️ SCADUTO';
            else if (diff === 0) scadTxt = ' — ⏰ OGGI';
            else if (diff === 1) scadTxt = ' — Domani';
        }
        return '<div class="postit-strip-item" data-priorita="' + (n.priorita || 'media') + '" style="background:' + bg + '" onclick="window._postit.toggle(true)">'
            + cat.icona + ' ' + escHtml(troncaTesto(n.testo, 60)) + scadTxt
            + '</div>';
    }).join('');
}

function updateBadge() {
    var badge = document.getElementById('postitBadge');
    if (!badge) return;
    if (notes.length > 0) {
        badge.textContent = notes.length;
        badge.classList.remove('hidden');
    } else {
        badge.classList.add('hidden');
    }
}

/* ===== PANEL TOGGLE ===== */
function togglePanel(forceOpen) {
    var panel = document.getElementById('postitPanel');
    if (!panel) return;
    if (forceOpen === true) {
        panelOpen = true;
    } else {
        panelOpen = !panelOpen;
    }
    panel.classList.toggle('visible', panelOpen);
    if (panelOpen) carica();
}

/* ===== STRIP TOGGLE ===== */
var stripVisible = true;
function toggleStrip() {
    stripVisible = !stripVisible;
    var strip = document.getElementById('postitStrip');
    if (strip) strip.classList.toggle('hidden', !stripVisible);
    // Salva preferenza
    try { localStorage.setItem('postit_strip', stripVisible ? '1' : '0'); } catch(e) {}
}

/* ===== FORM ===== */
function mostraForm(nota) {
    var form = document.getElementById('postitForm');
    form.classList.add('active');
    document.getElementById('postitTesto').value = nota ? nota.testo : '';
    document.getElementById('postitCategoria').value = nota ? nota.categoria : 'generico';
    document.getElementById('postitPriorita').value = nota ? nota.priorita : 'media';
    document.getElementById('postitScadenza').value = nota ? (nota.scadenza || '') : '';
    // Colore
    var col = nota ? nota.colore : 'giallo';
    document.querySelectorAll('#postitColori .postit-colore-dot').forEach(function(d) {
        d.classList.toggle('selected', d.dataset.colore === col);
    });
    editingId = nota ? nota.id : null;
    document.getElementById('postitTesto').focus();
}

function annullaForm() {
    document.getElementById('postitForm').classList.remove('active');
    editingId = null;
}

function getColoreSelezionato() {
    var sel = document.querySelector('#postitColori .postit-colore-dot.selected');
    return sel ? sel.dataset.colore : 'giallo';
}

/* ===== CRUD ===== */
function nuovo() {
    mostraForm(null);
}

function salva() {
    var testo = document.getElementById('postitTesto').value.trim();
    if (!testo) return;
    var body = {
        testo: testo,
        categoria: document.getElementById('postitCategoria').value,
        priorita: document.getElementById('postitPriorita').value,
        colore: getColoreSelezionato(),
        scadenza: document.getElementById('postitScadenza').value || null
    };

    if (editingId) {
        api('PUT', '/api/postit/' + editingId, body).then(function(res) {
            if (res.ok) { annullaForm(); carica(); _toast('Post-it aggiornato', 'success'); }
            else _toast(res.error || 'Errore', 'error');
        });
    } else {
        api('POST', '/api/postit', body).then(function(res) {
            if (res.ok) { annullaForm(); carica(); _toast('Post-it creato', 'success'); }
            else _toast(res.error || 'Errore', 'error');
        });
    }
}

function modifica(id) {
    var nota = notes.find(function(n) { return n.id === id; });
    if (nota) mostraForm(nota);
}

function elimina(id) {
    if (!confirm('Eliminare questo post-it?')) return;
    api('DELETE', '/api/postit/' + id).then(function(res) {
        if (res.ok) { carica(); _toast('Post-it eliminato', 'success'); }
        else _toast(res.error || 'Errore', 'error');
    });
}

/* ===== UTILS ===== */
function escHtml(s) {
    var d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
function troncaTesto(s, max) {
    if (!s) return '';
    return s.length > max ? s.substring(0, max) + '…' : s;
}
function _toast(msg, type) {
    // Riusa il toast della pagina se esiste
    if (typeof window.toast === 'function') { window.toast(msg, type); return; }
    var t = document.getElementById('toast');
    if (t) {
        t.textContent = msg;
        t.className = 'toast ' + (type || 'success') + ' show';
        setTimeout(function() { t.classList.remove('show'); }, 2500);
    }
}

/* ===== CALENDARIO INTEGRATION ===== */
function apriImportCalendario() {
    var overlay = document.getElementById('postitCalOverlay');
    if (!overlay) return;
    overlay.classList.add('visible');
    var lista = document.getElementById('postitCalLista');
    lista.innerHTML = '<div class="postit-cal-empty">⏳ Caricamento attività...</div>';
    api('GET', '/api/postit/calendario-attivita').then(function(res) {
        if (!res.ok || !res.attivita || res.attivita.length === 0) {
            lista.innerHTML = '<div class="postit-cal-empty">📭 Nessuna attività da importare</div>';
            return;
        }
        var catIcn = { appuntamento: '📍', promemoria: '🔔', scadenza: '⏰', riunione: '👥', boxbeat: '📦', altro: '📋' };
        lista.innerHTML = res.attivita.map(function(a) {
            var icn = catIcn[a.categoria] || '📋';
            var dataStr = a.data ? new Date(a.data + 'T00:00:00').toLocaleDateString('it-IT', { day: '2-digit', month: 'short' }) : '';
            var oraStr = a.ora ? ' · ' + a.ora : '';
            return [
                '<div class="postit-cal-item">',
                '  <div class="postit-cal-item-info">',
                '    <div class="postit-cal-item-title">' + icn + ' ' + escHtml(a.titolo) + '</div>',
                '    <div class="postit-cal-item-meta">' + dataStr + oraStr + ' · ' + (a.categoria || '') + '</div>',
                '  </div>',
                '  <button class="postit-cal-item-btn" onclick="window._postit.importaDaCalendario(\'' + a.id + '\')">📌 Fissa</button>',
                '</div>'
            ].join('');
        }).join('');
    }).catch(function() {
        lista.innerHTML = '<div class="postit-cal-empty">❌ Errore caricamento</div>';
    });
}

function chiudiImportCalendario() {
    var overlay = document.getElementById('postitCalOverlay');
    if (overlay) overlay.classList.remove('visible');
}

function importaDaCalendario(attivitaId) {
    api('POST', '/api/postit/from-calendario', { attivita_id: attivitaId }).then(function(res) {
        if (res.ok) {
            _toast('📌 Attività fissata come post-it', 'success');
            chiudiImportCalendario();
            carica();
        } else {
            _toast(res.error || 'Errore', 'error');
        }
    });
}

function syncCalendario(noteId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
    api('POST', '/api/postit/' + noteId + '/to-calendario').then(function(res) {
        if (res.ok) {
            var ora = new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
            _toast('📅 Aggiunto al calendario alle ' + ora, 'success');
            if (btn) { btn.textContent = '✅'; setTimeout(function() { btn.textContent = '⬆️'; btn.disabled = false; }, 2000); }
        } else {
            _toast(res.error || 'Errore', 'error');
            if (btn) { btn.textContent = '⬆️'; btn.disabled = false; }
        }
    }).catch(function() {
        _toast('Errore di rete', 'error');
        if (btn) { btn.textContent = '⬆️'; btn.disabled = false; }
    });
}

/* ===== INIT ===== */
function init() {
    injectCSS();
    injectHTML();

    // Restore strip preference
    try {
        var pref = localStorage.getItem('postit_strip');
        if (pref === '0') {
            stripVisible = false;
            var strip = document.getElementById('postitStrip');
            if (strip) strip.classList.add('hidden');
        }
    } catch(e) {}

    // Carica note
    carica();

    // Refresh ogni 60s
    setInterval(carica, 60000);
}

// Esponi API pubblica
window._postit = {
    toggle: togglePanel,
    toggleStrip: toggleStrip,
    nuovo: nuovo,
    salva: salva,
    modifica: modifica,
    elimina: elimina,
    annullaForm: annullaForm,
    carica: carica,
    apriImportCalendario: apriImportCalendario,
    chiudiImportCalendario: chiudiImportCalendario,
    importaDaCalendario: importaDaCalendario,
    syncCalendario: syncCalendario
};

// Auto-init
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

})();
