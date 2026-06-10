/**
 * Bacheca Promemoria — Sistema di notifiche e bacheca sempre visibile.
 * Sostituisce il vecchio sistema post-it.
 * Si auto-inietta CSS + HTML al caricamento.
 * 
 * Funzionalità:
 * 1) Pannello bacheca laterale con tutte le attività/promemoria dal calendario
 * 2) Modal auto-popup per promemoria della data corrente (al caricamento pagina)
 * 3) Modal auto-popup quando si cambia data nella sidebar (selectDateFromPicker, onDataFilterChange, ecc.)
 */
(function() {
'use strict';

/* ===== CSS ===== */
var CSS = `
/* Bacheca FAB */
.bacheca-fab {
    position: fixed;
    bottom: 70px;
    right: 12px;
    width: 48px;
    height: 48px;
    background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
    color: white;
    border: none;
    border-radius: 50%;
    cursor: pointer;
    font-size: 20px;
    z-index: 9998;
    box-shadow: 0 4px 15px rgba(99,102,241,0.45);
    transition: all .3s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    line-height: 1;
}
[data-theme="dark"] .bacheca-fab {
    background: linear-gradient(135deg, #6366f1 0%, #3730a3 100%);
}
.bacheca-fab:hover {
    transform: scale(1.1);
    box-shadow: 0 6px 22px rgba(99,102,241,0.6);
}
.bacheca-fab:active { transform: scale(.95); }
.bacheca-fab .bacheca-badge {
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
.bacheca-badge.hidden { display: none; }

/* Panel */
.bacheca-panel {
    position: fixed;
    bottom: 128px;
    right: 12px;
    width: 400px;
    max-height: 540px;
    background: var(--surface-raised, var(--bg-secondary, #1e293b));
    border: var(--surface-border, 1px solid var(--border, #334155));
    border-radius: 16px;
    box-shadow: var(--surface-shadow, 0 12px 40px rgba(0,0,0,.3));
    z-index: 9998;
    transform: translateY(20px);
    opacity: 0;
    pointer-events: none;
    transition: transform .3s cubic-bezier(.4,0,.2,1), opacity .3s cubic-bezier(.4,0,.2,1);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    will-change: transform, opacity;
}
.bacheca-panel.visible {
    transform: translateY(0);
    opacity: 1;
    pointer-events: all;
}

/* Panel header */
.bacheca-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: var(--surface-border, 1px solid var(--border, #334155));
    background: linear-gradient(135deg, var(--accent, #3b82f6) 0%, var(--accent-hover, var(--accent-hover, #60a5fa)) 100%);
    color: white;
}
.bacheca-panel-header .bacheca-panel-title { color: white; }
.bacheca-panel-title {
    font-weight: 700;
    font-size: .95em;
    color: var(--text-primary, #eee);
}
.bacheca-panel-actions {
    display: flex;
    gap: 6px;
}
.bacheca-panel-actions button, .bacheca-panel-actions a {
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
.bacheca-panel-actions button:hover, .bacheca-panel-actions a:hover {
    background: var(--border, #334155);
    color: var(--text-primary, #eee);
}

/* Notes list */
.bacheca-list {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.bacheca-list::-webkit-scrollbar { width: 5px; }
.bacheca-list::-webkit-scrollbar-thumb { background: var(--border, #334155); border-radius: 4px; }

.bacheca-empty {
    text-align: center;
    padding: 30px 20px;
    color: var(--text-muted, #64748b);
    font-size: .9em;
}

/* Single item card */
.bacheca-card {
    border-radius: 10px;
    padding: 10px 12px;
    position: relative;
    transition: all .2s;
    border-left: 4px solid transparent;
    cursor: default;
    background: var(--bg-tertiary, #0f172a);
}
.bacheca-card:hover {
    box-shadow: 0 2px 12px rgba(0,0,0,.15);
}
.bacheca-card[data-cat="scadenza"] { border-left-color: #ef4444; }
.bacheca-card[data-cat="promemoria"] { border-left-color: #f59e0b; }
.bacheca-card[data-cat="appuntamento"] { border-left-color: #3b82f6; }
.bacheca-card[data-cat="riunione"] { border-left-color: #8b5cf6; }
.bacheca-card[data-cat="altro"] { border-left-color: #64748b; }

.bacheca-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
}
.bacheca-card-cat {
    font-size: .75em;
    font-weight: 600;
    opacity: .8;
    display: flex;
    align-items: center;
    gap: 4px;
    color: var(--text-secondary, #94a3b8);
}
.bacheca-card-text {
    font-size: .88em;
    line-height: 1.4;
    color: var(--text-primary, #eee);
    word-break: break-word;
    white-space: pre-wrap;
}
.bacheca-card-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 6px;
    font-size: .7em;
    color: var(--text-secondary, #94a3b8);
}
.bacheca-card-data {
    font-weight: 600;
}
.bacheca-card-data.oggi {
    color: #ef4444;
}
.bacheca-card-data.passata {
    color: #f59e0b;
}

/* Category groups */
.bacheca-group {
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid rgba(255,255,255,.06);
}
.bacheca-group-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 7px 10px;
    cursor: pointer;
    user-select: none;
    transition: background .15s;
    background: var(--bg-tertiary, #0f172a);
    border-radius: 8px;
}
.bacheca-group-header:hover { opacity: .85; }
.bacheca-group-label {
    font-size: .8em;
    font-weight: 700;
    color: var(--text-primary, #eee);
    display: flex;
    align-items: center;
    gap: 5px;
}
.bacheca-group-count {
    background: rgba(255,255,255,.18);
    color: var(--text-primary, #eee);
    font-size: .7em;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 8px;
    min-width: 20px;
    text-align: center;
}
.bacheca-group-arrow {
    font-size: .7em;
    transition: transform .2s;
    color: var(--text-secondary, #94a3b8);
}
.bacheca-group.collapsed .bacheca-group-arrow {
    transform: rotate(-90deg);
}
.bacheca-group-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 6px 0 0 0;
}
.bacheca-group.collapsed .bacheca-group-body {
    display: none;
}

/* ===== MODAL PROMEMORIA AUTO-POPUP ===== */
.bacheca-modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,.55);
    z-index: 10001;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity .3s;
}
.bacheca-modal-overlay.visible {
    opacity: 1;
    pointer-events: all;
}
.bacheca-modal {
    background: var(--bg-secondary, #1e293b);
    border: 1px solid var(--border, #334155);
    border-radius: 16px;
    width: 480px;
    max-width: 94vw;
    max-height: 80vh;
    box-shadow: 0 20px 60px rgba(0,0,0,.5);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    animation: bacheca-modal-in .3s ease-out;
}
@keyframes bacheca-modal-in {
    from { transform: translateY(30px) scale(.95); opacity: 0; }
    to { transform: translateY(0) scale(1); opacity: 1; }
}
.bacheca-modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border, #334155);
    background: linear-gradient(135deg, rgba(239,68,68,0.1), rgba(245,158,11,0.1));
}
.bacheca-modal-header-title {
    font-weight: 800;
    font-size: 1.05em;
    color: var(--text-primary, #eee);
    display: flex;
    align-items: center;
    gap: 8px;
}
.bacheca-modal-header button {
    background: none;
    border: none;
    color: var(--text-secondary, #94a3b8);
    font-size: 18px;
    cursor: pointer;
    padding: 6px 8px;
    border-radius: 8px;
    transition: all .2s;
}
.bacheca-modal-header button:hover {
    background: var(--border, #334155);
    color: var(--text-primary, #eee);
}
.bacheca-modal-body {
    padding: 16px 20px;
    overflow-y: auto;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 10px;
}
.bacheca-modal-item {
    padding: 12px 14px;
    border-radius: 12px;
    background: var(--bg-tertiary, #0f172a);
    border-left: 4px solid #f59e0b;
    transition: all .2s;
}
.bacheca-modal-item[data-cat="scadenza"] { border-left-color: #ef4444; }
.bacheca-modal-item[data-cat="promemoria"] { border-left-color: #f59e0b; }
.bacheca-modal-item[data-cat="appuntamento"] { border-left-color: #3b82f6; }
.bacheca-modal-item[data-cat="riunione"] { border-left-color: #8b5cf6; }
.bacheca-modal-item[data-cat="altro"] { border-left-color: #64748b; }

.bacheca-modal-item-title {
    font-weight: 700;
    font-size: .92em;
    color: var(--text-primary, #eee);
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 6px;
}
.bacheca-modal-item-desc {
    font-size: .82em;
    color: var(--text-secondary, #94a3b8);
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
}
.bacheca-modal-item-meta {
    font-size: .72em;
    color: var(--text-muted, #64748b);
    margin-top: 6px;
    display: flex;
    gap: 10px;
}
.bacheca-modal-footer {
    padding: 12px 20px;
    border-top: 1px solid var(--border, #334155);
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.bacheca-modal-footer-info {
    font-size: .78em;
    color: var(--text-muted, #64748b);
}
.bacheca-modal-footer button {
    background: linear-gradient(135deg, #6366f1, #4f46e5);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: 700;
    font-size: .85em;
    cursor: pointer;
    transition: all .2s;
}
.bacheca-modal-footer button:hover {
    box-shadow: 0 4px 15px rgba(99,102,241,0.4);
    transform: translateY(-1px);
}
.bacheca-modal-mute {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    font-size: .78em;
    color: var(--text-secondary, #94a3b8);
    user-select: none;
    padding: 6px 0;
}
.bacheca-modal-mute input[type="checkbox"] {
    accent-color: #6366f1;
    width: 15px;
    height: 15px;
    cursor: pointer;
}
.bacheca-modal-mute:hover {
    color: var(--text-primary, #eee);
}

/* Responsive */
@media (max-width: 768px) {
    .bacheca-fab {
        width: 44px;
        height: 44px;
        right: 10px;
        bottom: 64px;
        font-size: 18px;
    }
    .bacheca-panel { bottom: 118px; }
}
@media (max-width: 600px) {
    .bacheca-panel { width: calc(100vw - 20px); right: 10px; bottom: 118px; }
    .bacheca-modal { width: 96vw; }
}
`;

/* ===== CONFIG ===== */
var CAT_ICONE = {
    appuntamento: '📍',
    promemoria: '🔔',
    scadenza: '⏰',
    riunione: '👥',
    altro: '📋'
};
var CAT_LABELS = {
    appuntamento: 'Appuntamento',
    promemoria: 'Promemoria',
    scadenza: 'Scadenza',
    riunione: 'Riunione',
    altro: 'Altro'
};

/* ===== STATE ===== */
var attivita = [];
var panelOpen = false;
var _modalDataMostrate = {}; // data → true, per non riaprire il modal più volte per la stessa data nella sessione
var _modalDataCorrente = null; // data attualmente mostrata nel modal
var _dateSilenziateKey = 'bacheca_date_silenziate';

function _getDateSilenziate() {
    try {
        var raw = localStorage.getItem(_dateSilenziateKey);
        return raw ? JSON.parse(raw) : {};
    } catch(e) { return {}; }
}
function _setDataSilenziata(data) {
    try {
        var s = _getDateSilenziate();
        s[data] = true;
        localStorage.setItem(_dateSilenziateKey, JSON.stringify(s));
    } catch(e) {}
}

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
    fab.className = 'bacheca-fab';
    fab.id = 'bachecaFab';
    fab.title = 'Bacheca Promemoria';
    fab.innerHTML = '📋<span class="bacheca-badge hidden" id="bachecaBadge">0</span>';
    fab.onclick = togglePanel;
    document.body.appendChild(fab);

    // Panel
    var panel = document.createElement('div');
    panel.className = 'bacheca-panel';
    panel.id = 'bachecaPanel';
    panel.innerHTML = [
        '<div class="bacheca-panel-header">',
        '  <div class="bacheca-panel-title">📋 Bacheca Promemoria</div>',
        '  <div class="bacheca-panel-actions">',
        '    <a href="/calendario" target="_blank" title="Apri Calendario">📅</a>',
        '    <button onclick="window._bacheca.toggle()" title="Chiudi">✕</button>',
        '  </div>',
        '</div>',
        '<div class="bacheca-list" id="bachecaList">',
        '  <div class="bacheca-empty">⏳ Caricamento...</div>',
        '</div>'
    ].join('\n');
    document.body.appendChild(panel);

    // Modal overlay (promemoria popup)
    var overlay = document.createElement('div');
    overlay.className = 'bacheca-modal-overlay';
    overlay.id = 'bachecaModalOverlay';
    overlay.innerHTML = [
        '<div class="bacheca-modal">',
        '  <div class="bacheca-modal-header">',
        '    <div class="bacheca-modal-header-title"><span>⚠️</span> <span id="bachecaModalTitle">Promemoria</span></div>',
        '    <button onclick="window._bacheca.chiudiModal()">✕</button>',
        '  </div>',
        '  <div class="bacheca-modal-body" id="bachecaModalBody">',
        '  </div>',
        '  <div class="bacheca-modal-footer">',
        '    <div>',
        '      <span class="bacheca-modal-footer-info" id="bachecaModalInfo"></span>',
        '      <label class="bacheca-modal-mute"><input type="checkbox" id="bachecaMuteCheck"> Non ricordarmelo più per questa giornata</label>',
        '    </div>',
        '    <button onclick="window._bacheca.chiudiModal()">Ho capito</button>',
        '  </div>',
        '</div>'
    ].join('\n');
    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) chiudiModal();
    });
    document.body.appendChild(overlay);
}

/* ===== API ===== */
function api(url) {
    return fetch(url).then(function(r) { return r.json(); });
}

/* ===== CARICAMENTO BACHECA ===== */
function caricaBacheca() {
    return api('/api/bacheca/tutti').then(function(res) {
        if (res.ok) {
            attivita = res.attivita || [];
            renderPanel();
            updateBadge();
        }
    }).catch(function(e) {
        console.error('[Bacheca] Errore caricamento:', e);
    });
}

/* ===== RENDER PANEL ===== */
function renderCard(a) {
    var cat = a.categoria || 'altro';
    var icona = CAT_ICONE[cat] || '📋';
    var label = CAT_LABELS[cat] || 'Altro';
    var dataStr = a.data ? formatDataIT(a.data) : '';
    var oraStr = a.ora ? ' · ' + a.ora : '';
    var statoData = getStatoData(a.data);

    return [
        '<div class="bacheca-card" data-cat="' + esc(cat) + '">',
        '  <div class="bacheca-card-header">',
        '    <span class="bacheca-card-cat">' + icona + ' ' + esc(label) + '</span>',
        '  </div>',
        '  <div class="bacheca-card-text">' + esc(a.titolo || '') + '</div>',
        a.descrizione ? '  <div class="bacheca-card-text" style="font-size:.78em;color:var(--text-secondary,#94a3b8);margin-top:4px">' + esc(troncaTesto(a.descrizione, 100)) + '</div>' : '',
        '  <div class="bacheca-card-footer">',
        '    <span>' + esc(a.creato_da || '') + '</span>',
        '    <span class="bacheca-card-data ' + statoData + '">' + dataStr + oraStr + '</span>',
        '  </div>',
        '</div>'
    ].join('');
}

var _collapsedGroups = {};
try {
    var saved = localStorage.getItem('bacheca_collapsed');
    if (saved) _collapsedGroups = JSON.parse(saved);
} catch(e) {}

function toggleGroup(catKey) {
    _collapsedGroups[catKey] = !_collapsedGroups[catKey];
    try { localStorage.setItem('bacheca_collapsed', JSON.stringify(_collapsedGroups)); } catch(e) {}
    renderPanel();
}

function renderPanel() {
    var list = document.getElementById('bachecaList');
    if (!list) return;

    if (attivita.length === 0) {
        list.innerHTML = '<div class="bacheca-empty">📋 Nessun promemoria attivo.<br><small>Crea promemoria dal <a href="/calendario" target="_blank" style="color:#6366f1">Calendario</a></small></div>';
        return;
    }

    // Raggruppa per categoria
    var gruppi = {};
    var ordine = ['scadenza', 'promemoria', 'appuntamento', 'riunione', 'altro'];
    attivita.forEach(function(a) {
        var k = a.categoria || 'altro';
        if (!gruppi[k]) gruppi[k] = [];
        gruppi[k].push(a);
    });

    // Ordina le categorie secondo ordine predefinito
    var ordineEffettivo = ordine.filter(function(k) { return gruppi[k] && gruppi[k].length > 0; });

    list.innerHTML = ordineEffettivo.map(function(catKey) {
        var icona = CAT_ICONE[catKey] || '📋';
        var label = CAT_LABELS[catKey] || catKey;
        var items = gruppi[catKey];
        var collapsed = _collapsedGroups[catKey] ? ' collapsed' : '';
        return [
            '<div class="bacheca-group' + collapsed + '" data-cat="' + catKey + '">',
            '  <div class="bacheca-group-header" onclick="window._bacheca.toggleGroup(\'' + catKey + '\')">',
            '    <span class="bacheca-group-label">' + icona + ' ' + label + '</span>',
            '    <div style="display:flex;align-items:center;gap:6px">',
            '      <span class="bacheca-group-count">' + items.length + '</span>',
            '      <span class="bacheca-group-arrow">▼</span>',
            '    </div>',
            '  </div>',
            '  <div class="bacheca-group-body">',
                 items.map(renderCard).join(''),
            '  </div>',
            '</div>'
        ].join('');
    }).join('');
}

/* ===== BADGE ===== */
function updateBadge() {
    var badge = document.getElementById('bachecaBadge');
    if (!badge) return;
    if (attivita.length > 0) {
        badge.textContent = attivita.length;
        badge.classList.remove('hidden');
    } else {
        badge.classList.add('hidden');
    }
}

/* ===== TOGGLE PANEL ===== */
function togglePanel(forceOpen) {
    var panel = document.getElementById('bachecaPanel');
    if (!panel) return;
    if (forceOpen === true) {
        panelOpen = true;
    } else {
        panelOpen = !panelOpen;
    }
    panel.classList.toggle('visible', panelOpen);
    if (panelOpen) caricaBacheca();
}

/* ===== MODAL PROMEMORIA ===== */
function mostraModalPromemoria(data, promemoria) {
    if (!promemoria || promemoria.length === 0) return;

    var overlay = document.getElementById('bachecaModalOverlay');
    var body = document.getElementById('bachecaModalBody');
    var title = document.getElementById('bachecaModalTitle');
    var info = document.getElementById('bachecaModalInfo');
    if (!overlay || !body) return;

    var oggi = getOggi();
    var isOggi = data === oggi;
    var isPast = data < oggi;
    var dataFormatted = formatDataIT(data);

    if (isOggi) {
        title.textContent = 'Promemoria per OGGI — ' + dataFormatted;
    } else if (isPast) {
        title.textContent = 'Promemoria scaduti — ' + dataFormatted;
    } else {
        title.textContent = 'Promemoria per il ' + dataFormatted;
    }

    body.innerHTML = promemoria.map(function(a) {
        var cat = a.categoria || 'altro';
        var icona = CAT_ICONE[cat] || '📋';
        var label = CAT_LABELS[cat] || 'Altro';
        var oraStr = a.ora ? a.ora + (a.ora_fine ? ' - ' + a.ora_fine : '') : '';

        return [
            '<div class="bacheca-modal-item" data-cat="' + esc(cat) + '">',
            '  <div class="bacheca-modal-item-title">' + icona + ' ' + esc(a.titolo || '') + '</div>',
            a.descrizione ? '  <div class="bacheca-modal-item-desc">' + esc(a.descrizione) + '</div>' : '',
            '  <div class="bacheca-modal-item-meta">',
            oraStr ? '    <span>🕐 ' + esc(oraStr) + '</span>' : '',
            '    <span>' + esc(label) + '</span>',
            a.creato_da && a.creato_da !== 'sistema' ? '    <span>👤 ' + esc(a.creato_da) + '</span>' : '',
            '  </div>',
            '</div>'
        ].join('');
    }).join('');

    info.textContent = promemoria.length + ' promemoria per questa data';

    _modalDataCorrente = data;
    var muteCheck = document.getElementById('bachecaMuteCheck');
    if (muteCheck) muteCheck.checked = false;

    overlay.classList.add('visible');
}

function chiudiModal() {
    var muteCheck = document.getElementById('bachecaMuteCheck');
    if (muteCheck && muteCheck.checked && _modalDataCorrente) {
        _setDataSilenziata(_modalDataCorrente);
    }
    _modalDataCorrente = null;
    var overlay = document.getElementById('bachecaModalOverlay');
    if (overlay) overlay.classList.remove('visible');
}

/* ===== CONTROLLO PROMEMORIA PER DATA ===== */
function controllaPromemoriaPerData(data) {
    if (!data) return;

    // Non mostrare se già mostrato in questa sessione per questa data
    var cacheKey = data;
    if (_modalDataMostrate[cacheKey]) return;

    // Non mostrare se l'utente ha silenziato questa data
    if (_getDateSilenziate()[data]) return;

    api('/api/bacheca/promemoria?data=' + encodeURIComponent(data)).then(function(res) {
        if (res.ok && res.promemoria && res.promemoria.length > 0) {
            _modalDataMostrate[cacheKey] = true;
            mostraModalPromemoria(data, res.promemoria);
        }
    }).catch(function(e) {
        console.error('[Bacheca] Errore controllo promemoria:', e);
    });
}

/* ===== HOOK NELLE FUNZIONI DI CAMBIO DATA ===== */
function hookDateChanges() {
    // Hook: selectDateFromPicker(data)
    if (typeof window.selectDateFromPicker === 'function') {
        var originalSelectDate = window.selectDateFromPicker;
        window.selectDateFromPicker = function(data) {
            originalSelectDate(data);
            controllaPromemoriaPerData(data);
        };
    }

    // Hook: navigateDate(direction) — intercetta la navigazione date
    if (typeof window.navigateDate === 'function') {
        var originalNavigateDate = window.navigateDate;
        window.navigateDate = function(direction) {
            originalNavigateDate(direction);
            // Dopo la navigazione, currentData dovrebbe essere aggiornato
            if (typeof window.currentData !== 'undefined' && window.currentData) {
                controllaPromemoriaPerData(window.currentData);
            }
        };
    }

    // Hook: onDataFilterChange() — filtro data sidebar
    if (typeof window.onDataFilterChange === 'function') {
        var originalOnDataFilter = window.onDataFilterChange;
        window.onDataFilterChange = function() {
            originalOnDataFilter();
            var dataFilter = document.getElementById('dataFilter');
            if (dataFilter && dataFilter.value) {
                controllaPromemoriaPerData(dataFilter.value);
            }
        };
    }
}

/* ===== UTILITIES ===== */
function esc(s) {
    var d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

function troncaTesto(s, max) {
    if (!s) return '';
    return s.length > max ? s.substring(0, max) + '…' : s;
}

function getOggi() {
    var d = new Date();
    return d.getFullYear() + '-' +
        String(d.getMonth() + 1).padStart(2, '0') + '-' +
        String(d.getDate()).padStart(2, '0');
}

function formatDataIT(data) {
    if (!data) return '';
    try {
        var d = new Date(data + 'T00:00:00');
        return d.toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
    } catch(e) {
        return data;
    }
}

function getStatoData(data) {
    if (!data) return '';
    var oggi = getOggi();
    if (data === oggi) return 'oggi';
    if (data < oggi) return 'passata';
    return '';
}

function _toast(msg, type) {
    if (typeof window.toast === 'function') { window.toast(msg, type); return; }
    var t = document.getElementById('toast');
    if (t) {
        t.textContent = msg;
        t.className = 'toast ' + (type || 'success') + ' show';
        setTimeout(function() { t.classList.remove('show'); }, 2500);
    }
}

/* ===== INIT ===== */
function init() {
    injectCSS();
    injectHTML();

    // Carica la bacheca
    caricaBacheca();

    // Controlla promemoria per oggi (auto-popup all'accesso)
    controllaPromemoriaPerData(getOggi());

    // Hook nel cambio date della mappa
    // Ritarda per assicurarsi che le funzioni della mappa siano definite
    setTimeout(hookDateChanges, 1500);

    // Refresh ogni 2 minuti
    setInterval(caricaBacheca, 120000);
}

// Esponi API pubblica
window._bacheca = {
    toggle: togglePanel,
    carica: caricaBacheca,
    chiudiModal: chiudiModal,
    toggleGroup: toggleGroup,
    controllaPromemoria: controllaPromemoriaPerData
};

// Auto-init
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

})();
