// -*- coding: utf-8 -*-
let generazioni = [];
let aziende = [];
let selectedGenerazione = '';
let TEMPLATES = {};
let FIELD_DEFS = {};
let templateSettings = {};
let allegatiInfo = {};
let bulkItems = [];
let bulkPreviews = [];
let bulkAbort = false;
let aziendeController = null;
let researchContactKey = '';
let filterIncompleteOnly = false;

const PAUSA_TRA_EMAIL_SEC = 4;
const RESEARCH_AUTO_OPEN_KEY = 'lettere_research_auto_open';

function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

function showToast(msg, error) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.borderColor = error ? 'var(--danger)' : 'var(--border)';
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2600);
}

function fmtTs(ts) {
    if (!ts) return '-';
    const d = new Date(Number(ts) * 1000);
    return d.toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

const MESI_IT = ['GENNAIO','FEBBRAIO','MARZO','APRILE','MAGGIO','GIUGNO',
                 'LUGLIO','AGOSTO','SETTEMBRE','OTTOBRE','NOVEMBRE','DICEMBRE'];

function periodoLabel(g) {
    if (!g || !g.periodo_da) return g?.periodo_da || '?';
    const [aY, aM, aD] = g.periodo_da.split('-').map(Number);
    if (!g.periodo_a) return `${aD} ${MESI_IT[aM - 1]}`;
    const [bY, bM, bD] = g.periodo_a.split('-').map(Number);
    if (aM === bM && aY === bY) return `${aD}-${bD} ${MESI_IT[aM - 1]}`;
    return `${aD} ${MESI_IT[aM - 1]} - ${bD} ${MESI_IT[bM - 1]}`;
}

function isPec(email) {
    if (!email) return false;
    const e = email.toLowerCase().trim();
    return /(@pec\.|@pec-|\.pec@|@legalmail\.|\.legalmail\.|@cert\.|@postacert\.|@cgn\.legalmail|@arubapec\.|@pcert\.|@sicurezzapostale\.|@certifiposta\.|@interfreepec\.)/.test(e)
        || e.endsWith('.pec.it')
        || e.endsWith('@pec.it');
}

function splitEmails(campo) {
    if (!campo) return [];
    const seen = new Set();
    const out = [];
    for (const part of String(campo).split(/[\/,;\s]+/)) {
        const email = part.trim();
        if (!email.includes('@')) continue;
        const key = email.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ email, pec: isPec(email) });
    }
    return out;
}

function propagateContactLocally(nominativo, comune, telefono, email, updatedKeys) {
    const currentNom = (nominativo || '').trim().toUpperCase();
    const currentCom = (comune || '').trim().toUpperCase();
    const keysSet = new Set(updatedKeys || []);
    for (const item of aziende) {
        const itemNom = (item.nominativo || '').trim().toUpperCase();
        const itemCom = (item.comune || '').trim().toUpperCase();
        if (keysSet.has(item.contact_key) ||
            (itemNom === currentNom && (!currentCom || !itemCom || itemCom === currentCom))) {
            if (telefono !== null && telefono !== undefined) item.telefono = telefono;
            if (email !== null && email !== undefined) item.email = email;
        }
    }
}

async function loadAll() {
    await Promise.all([loadGenerazioni(), loadTemplates(), loadAllegati(), loadStats()]);
    await loadAziende();
}

async function loadGenerazioni() {
    const res = await fetch('/api/lettere/generazioni');
    const data = await res.json();
    if (!data.success) throw new Error(data.error || 'Errore generazioni');
    generazioni = data.generazioni || [];
    renderGenerazioni();
}

async function loadTemplates() {
    const data = await BulkEmailPreview.loadCentralizedTemplates('/api/distinte/templates');
    templateSettings = data.settings || {};
    FIELD_DEFS = data.fieldDefs || {};
    TEMPLATES = data.templates || {};

    const select = document.getElementById('templateSelect');
    select.innerHTML = '';
    for (const [id, tmpl] of Object.entries(TEMPLATES)) {
        if (tmpl.type !== 'email') continue;
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = tmpl.label || id;
        select.appendChild(opt);
    }
}

function onTemplateChange() {
    if (document.getElementById('bulkPreviewSection') && document.getElementById('bulkPreviewSection').style.display === 'block') {
        generaAnteprima();
    }
}

async function loadAllegati() {
    const res = await fetch('/api/lettere/allegati');
    allegatiInfo = await res.json();
}

async function loadStats() {
    const res = await fetch('/api/lettere/statistiche');
    const data = await res.json();
    const s = data.lettere || {};
    document.getElementById('statsBox').innerHTML = `
        <div class="kpi"><span>${s.generazioni || 0}</span><small>Generazioni</small></div>
        <div class="kpi"><span>${s.aziende || 0}</span><small>Aziende</small></div>
        <div class="kpi"><span>${s.copertura_contatti || 0}%</span><small>Con email/PEC</small></div>
        <div class="kpi"><span>${s.tasso_feedback || 0}%</span><small>Feedback</small></div>
    `;
}

function renderGenerazioni() {
    const list = document.getElementById('generazioniList');
    if (!generazioni.length) {
        list.innerHTML = '<div class="empty">Nessuna generazione archiviata</div>';
        return;
    }
    list.innerHTML = generazioni.map(g => `
        <div class="generation-item ${String(selectedGenerazione) === String(g.id) ? 'active' : ''}"
             role="button" tabindex="0"
             onclick="selectGenerazione('${g.id}')"
             onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectGenerazione('${g.id}'); }">
            <strong>${esc(periodoLabel(g))}</strong>
            <span>${g.totale_aziende || 0} aziende</span>
            <span>
                ${fmtTs(g.created_ts)}
                <a href="/api/lettere/generazioni/${g.id}/download" onclick="event.stopPropagation()">Scarica</a>
            </span>
        </div>
    `).join('');
    document.getElementById('genAllBtn').classList.toggle('active', selectedGenerazione === '');
}

async function selectGenerazione(id) {
    selectedGenerazione = id || '';
    renderGenerazioni();
    await loadAziende();
}

async function loadAziende() {
    if (aziendeController) aziendeController.abort();
    aziendeController = new AbortController();
    const body = document.getElementById('aziendeBody');
    body.innerHTML = '<tr><td colspan="6" class="loading"><i class="fas fa-spinner fa-spin"></i> Caricamento...</td></tr>';
    const q = document.getElementById('searchInput').value.trim();
    const params = new URLSearchParams();
    if (selectedGenerazione) params.set('generazione_id', selectedGenerazione);
    if (q) params.set('q', q);
    params.set('limit', '5000');
    try {
        const res = await fetch(`/api/lettere/aziende?${params.toString()}`, {
            signal: aziendeController.signal
        });
        const data = await res.json();
        if (!data.success) {
            showToast(data.error || 'Errore caricamento aziende', true);
            return;
        }
        aziende = data.aziende || [];
        renderAziende();
        updateBulkButton();
    } catch (e) {
        if (e.name !== 'AbortError') {
            showToast(e.message || 'Errore caricamento aziende', true);
        }
    }
}

function renderEmailBadges(emailField) {
    const emails = splitEmails(emailField);
    if (!emails.length) return '<div class="mini">Nessun indirizzo</div>';
    return `<div class="badges">${emails.map(e =>
        `<span class="badge ${e.pec ? 'pec' : ''}">${e.pec ? 'PEC' : 'MAIL'}</span>`
    ).join('')}</div>`;
}

function hasMissingContact(a) {
    return !(a.telefono || '').trim() || splitEmails(a.email).length === 0;
}

function researchQueue() {
    // Ordina per data (più vicina prima) poi alfabetico, dedup per nominativo
    const today = new Date().toISOString().slice(0, 10);
    const sorted = [...aziende]
        .filter(a => hasMissingContact(a))
        .sort((a, b) => {
            const dA = parseDateToISO(a.data_pianificazione) || '9999-99-99';
            const dB = parseDateToISO(b.data_pianificazione) || '9999-99-99';
            // Date più vicine a oggi prima (ordine crescente)
            if (dA !== dB) return dA < dB ? -1 : 1;
            return (a.nominativo || '').localeCompare(b.nominativo || '', 'it', { sensitivity: 'base' });
        });
    const seen = new Set();
    return sorted.filter(a => {
        const key = (a.nominativo || '').trim().toUpperCase();
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function googleSearchUrl(query) {
    return `https://www.google.com/search?q=${encodeURIComponent(query)}`;
}

function getValidCoords(a) {
    if (!a) return null;
    const lat = Number(a.latitudine);
    const lon = Number(a.longitudine);
    if (!isNaN(lat) && !isNaN(lon) && lat >= 35 && lat <= 48 && lon >= 6 && lon <= 20 && (lat !== 0 || lon !== 0)) {
        return { lat, lon };
    }
    return null;
}

function buildSearchTerms(a) {
    const nome = (a.nominativo || '').trim();
    const comune = (a.comune || '').trim();
    const nomeBreve = nome.replace(
        /\s+(S\.?R\.?L\.?S?\.?|S\.?P\.?A\.?|S\.?A\.?S\.?|S\.?N\.?C\.?|S\.?C\.?A\.?R\.?L\.?|S\.?D\.?F\.?)$/i,
        ''
    ).trim();
    const loose = [nome, comune].filter(Boolean).join(' ');
    const looseBreve = [nomeBreve || nome, comune].filter(Boolean).join(' ');
    const focused = [nome.includes(' ') ? `"${nome}"` : nome, comune].filter(Boolean).join(' ');
    return { nome, comune, loose, looseBreve, focused };
}

function buildResearchGroups(a) {
    const { nome, comune, loose, looseBreve, focused } = buildSearchTerms(a);
    const coords = getValidCoords(a);
    const indirizzo = [a.via, a.civico].filter(Boolean).join(' ').trim();
    const queryIndirizzo = [indirizzo, comune].filter(Boolean).join(' ');

    const mapLinks = [];
    if (coords) {
        mapLinks.push({
            label: 'Posizione contatore (Maps)',
            icon: 'fa-location-dot',
            url: `https://www.google.com/maps?q=${coords.lat},${coords.lon}`
        });
    } else if (queryIndirizzo) {
        mapLinks.push({
            label: 'Google Maps (Indirizzo)',
            icon: 'fa-map-pin',
            url: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(queryIndirizzo)}`
        });
    }

    mapLinks.push({
        label: 'Google Maps (Azienda)',
        icon: 'fa-map-location-dot',
        url: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(loose)}`
    });
    mapLinks.push({ label: 'LinkedIn', icon: 'fa-linkedin', brand: true, url: googleSearchUrl(`site:linkedin.com ${looseBreve}`) });
    mapLinks.push({ label: 'Facebook', icon: 'fa-facebook', brand: true, url: googleSearchUrl(`site:facebook.com ${looseBreve}`) });
    mapLinks.push({ label: 'Instagram', icon: 'fa-instagram', brand: true, url: googleSearchUrl(`site:instagram.com ${looseBreve}`) });

    return [
        {
            title: 'Google',
            links: [
                { label: 'Tutti i contatti', icon: 'fa-magnifying-glass', url: googleSearchUrl(`${loose} contatti telefono email`) },
                { label: 'Ricerca ampia', icon: 'fa-binoculars', url: googleSearchUrl(`${looseBreve} contatti`) },
                { label: 'Telefono', icon: 'fa-phone', url: googleSearchUrl(`${loose} telefono`) },
                { label: 'Email', icon: 'fa-envelope', url: googleSearchUrl(`${loose} email`) },
                { label: 'PEC', icon: 'fa-at', url: googleSearchUrl(`${looseBreve} PEC`) },
                { label: 'Sito ufficiale', icon: 'fa-globe', url: googleSearchUrl(`${loose} sito ufficiale`) },
            ]
        },
        {
            title: 'Mappe e social',
            links: mapLinks
        },
        {
            title: 'Fonti ufficiali e elenchi',
            links: [
                {
                    label: 'INI-PEC',
                    icon: 'fa-shield-halved',
                    url: googleSearchUrl(`site:inipec.gov.it ${looseBreve}`)
                },
                { label: 'Registro Imprese', icon: 'fa-landmark', url: googleSearchUrl(`site:registroimprese.it ${looseBreve}`) },
                {
                    label: 'Ufficio Camerale',
                    icon: 'fa-building',
                    url: googleSearchUrl(`site:ufficiocamerale.it ${looseBreve}`)
                },
                { label: 'Pagine Gialle', icon: 'fa-address-book', url: googleSearchUrl(`site:paginegialle.it ${looseBreve}`) },
                { label: 'Pagine Bianche', icon: 'fa-book-open', url: googleSearchUrl(`site:paginebianche.it ${looseBreve}`) },
            ]
        }
    ];
}

function currentResearchContact() {
    return aziende.find(a => a.contact_key === researchContactKey);
}

function openContactResearch(id, autoOpen) {
    const row = aziende.find(a => Number(a.id) === Number(id));
    if (!row) return;
    researchContactKey = row.contact_key;
    renderContactResearch();
    document.getElementById('researchModal').classList.add('active');
    if (autoOpen || autoOpenResearchesEnabled()) {
        openPrimaryResearches();
    }
}

function closeContactResearch() {
    document.getElementById('researchModal').classList.remove('active');
    researchContactKey = '';
}

function renderContactResearch() {
    const row = currentResearchContact();
    if (!row) {
        closeContactResearch();
        return;
    }
    const queue = researchQueue();
    const currentNom = (row.nominativo || '').trim().toUpperCase();
    const queueIndex = queue.findIndex(a => (a.nominativo || '').trim().toUpperCase() === currentNom);
    const missing = [
        !(row.telefono || '').trim() ? 'telefono' : '',
        splitEmails(row.email).length === 0 ? 'email/PEC' : ''
    ].filter(Boolean);

    const indirizzo = [row.via, row.civico].filter(Boolean).join(' ').trim();
    const coords = getValidCoords(row);

    document.getElementById('researchCompanyName').textContent = row.nominativo || 'Azienda';
    
    const metaParts = [];
    if (row.comune) metaParts.push(row.comune);
    if (indirizzo) metaParts.push(indirizzo);
    if (row.pod) metaParts.push(row.pod);
    metaParts.push(missing.length ? `Manca: ${missing.join(', ')}` : 'Recapiti completi');
    document.getElementById('researchCompanyMeta').textContent = metaParts.join(' · ');

    const actionsEl = document.getElementById('researchCompanyActions');
    if (actionsEl) {
        if (coords) {
            const mapsUrl = `https://www.google.com/maps?q=${coords.lat},${coords.lon}`;
            actionsEl.innerHTML = `
                <a class="btn-maps-coord" href="${esc(mapsUrl)}" target="_blank" rel="noopener noreferrer"
                   title="Vedi coordinate contatore (${coords.lat.toFixed(6)}, ${coords.lon.toFixed(6)}) su Google Maps">
                    <i class="fas fa-location-dot"></i>
                    <span>Posizione contatore</span>
                    <i class="fas fa-arrow-up-right-from-square mini-icon"></i>
                </a>
            `;
        } else {
            const queryIndirizzo = [indirizzo, row.comune].filter(Boolean).join(', ');
            const fallbackUrl = queryIndirizzo
                ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(queryIndirizzo)}`
                : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((row.nominativo || '') + ' ' + (row.comune || ''))}`;
            actionsEl.innerHTML = `
                <a class="btn-maps-coord no-coord" href="${esc(fallbackUrl)}" target="_blank" rel="noopener noreferrer"
                   title="Coordinate contatore non note — Cerca indirizzo su Google Maps">
                    <i class="fas fa-map-location-dot"></i>
                    <span>Cerca su Maps</span>
                    <i class="fas fa-arrow-up-right-from-square mini-icon"></i>
                </a>
            `;
        }
    }

    document.getElementById('researchProgress').textContent = queue.length
        ? `${queueIndex >= 0 ? queueIndex + 1 : '-'} di ${queue.length} contatti incompleti nella lista corrente`
        : 'Nessun altro contatto incompleto nella lista corrente';
    document.getElementById('researchPhone').value = row.telefono || '';
    document.getElementById('researchEmail').value = row.email || '';
    document.getElementById('researchGroups').innerHTML = buildResearchGroups(row).map(group => `
        <section class="research-group">
            <h3>${esc(group.title)}</h3>
            <div class="research-links">
                ${group.links.map(link => `
                    <a class="research-link" href="${esc(link.url)}" target="_blank" rel="noopener noreferrer">
                        <i class="${link.brand ? 'fa-brands' : 'fas'} ${link.icon}"></i>
                        <span>${esc(link.label)}</span>
                        <i class="fas fa-arrow-up-right-from-square"></i>
                    </a>
                `).join('')}
            </div>
        </section>
    `).join('');
}

function autoOpenResearchesEnabled() {
    const el = document.getElementById('researchAutoOpen');
    return !el || el.checked;
}

function getPrimaryResearchLinks(row) {
    const googleLinks = buildResearchGroups(row)[0].links;
    const missingPhone = !(row.telefono || '').trim();
    const missingEmail = splitEmails(row.email).length === 0;
    const links = [];
    if (missingPhone && missingEmail) {
        links.push(googleLinks[0]);
    } else if (missingPhone || missingEmail) {
        links.push(googleLinks[1]);
    }
    if (missingPhone) {
        links.push(googleLinks[2]);
    }
    if (missingEmail) {
        links.push(googleLinks[3], googleLinks[4]);
    }
    return links.length ? links : [googleLinks[0], googleLinks[1]];
}

function openPrimaryResearchesFor(row) {
    if (!row) return;
    const links = getPrimaryResearchLinks(row);
    for (const link of links) {
        window.open(link.url, '_blank', 'noopener,noreferrer');
    }
    const labels = links.map(link => link.label.toLowerCase()).join(', ');
    showToast(`Aperte ${links.length} ricerche: ${labels}`);
}

function openPrimaryResearches() {
    openPrimaryResearchesFor(currentResearchContact());
}

function startContactResearchQueue() {
    const queue = researchQueue();
    if (!queue.length) {
        showToast('Nessun contatto incompleto nella lista corrente');
        return;
    }
    openContactResearch(queue[0].id, true);
}

function navigateResearch(delta) {
    const queue = researchQueue();
    const row = currentResearchContact();
    if (!row || !queue.length) return;
    const currentNom = (row.nominativo || '').trim().toUpperCase();
    const currentIndex = queue.findIndex(a => (a.nominativo || '').trim().toUpperCase() === currentNom);
    if (currentIndex < 0) return;
    const next = queue[(currentIndex + delta + queue.length) % queue.length];
    researchContactKey = next.contact_key;
    renderContactResearch();
    if (autoOpenResearchesEnabled()) {
        openPrimaryResearches();
    }
}

async function saveResearchContact(openNext) {
    const row = currentResearchContact();
    if (!row) return;
    const currentNom = (row.nominativo || '').trim().toUpperCase();
    const currentCom = (row.comune || '').trim().toUpperCase();
    const telefono = document.getElementById('researchPhone').value.trim();
    const email = document.getElementById('researchEmail').value.trim();

    // Aggiorna subito lo stato locale prima di cercare il prossimo contatto incompleto
    row.telefono = telefono;
    row.email = email;
    propagateContactLocally(row.nominativo, row.comune, telefono, email);

    const queue = researchQueue();
    const nextRow = queue.length > 0 ? queue[0] : null;

    // Apri subito le ricerche del prossimo contatto, mentre il click utente
    // è ancora valido per i popup del browser.
    if (openNext && nextRow && autoOpenResearchesEnabled()) {
        openPrimaryResearchesFor(nextRow);
    }

    try {
        // Salva per nominativo (e comune se presente)
        const res = await fetch('/api/lettere/contatti-by-name', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nominativo: row.nominativo, comune: row.comune, telefono, email })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Errore salvataggio');

        propagateContactLocally(row.nominativo, row.comune, telefono, email, data.updated_keys);
        renderAziende();
        updateBulkButton();
        loadStats();
        const count = data.updated_count || 1;
        const msg = count > 1
            ? `Salvato per ${count} contatori con lo stesso nominativo`
            : (data.synced_filone ? 'Salvato anche sul filone' : 'Contatto salvato');
        showToast(msg);

        if (openNext && nextRow) {
            researchContactKey = nextRow.contact_key;
            renderContactResearch();
        } else if (openNext) {
            closeContactResearch();
            showToast('Nessun altro contatto incompleto nella lista corrente');
        } else {
            renderContactResearch();
        }
    } catch (e) {
        showToast(e.message, true);
    }
}

function visibleAziende() {
    if (!filterIncompleteOnly) return aziende;
    return aziende.filter(hasMissingContact);
}

function toggleIncompleteFilter(checked) {
    filterIncompleteOnly = Boolean(checked);
    renderAziende();
}

const GIORNI_IT = ['Domenica','Lunedì','Martedì','Mercoledì','Giovedì','Venerdì','Sabato'];
const MESI_IT_LOWER = ['gennaio','febbraio','marzo','aprile','maggio','giugno',
                       'luglio','agosto','settembre','ottobre','novembre','dicembre'];
let collapsedDates = new Set();

function parseDateToISO(dateStr) {
    if (!dateStr) return '';
    if (dateStr.includes('/')) {
        const [d, m, y] = dateStr.split('/');
        return `${y}-${m.padStart(2, '0')}-${d.padStart(2, '0')}`;
    }
    return dateStr;
}

function formatDateGroupHeader(dateStr) {
    if (!dateStr) return 'Data non specificata';
    let d, m, y;
    if (dateStr.includes('/')) {
        [d, m, y] = dateStr.split('/').map(Number);
    } else {
        [y, m, d] = dateStr.split('-').map(Number);
    }
    const dt = new Date(y, m - 1, d);
    return `${GIORNI_IT[dt.getDay()]} ${d} ${MESI_IT_LOWER[m - 1]} ${y}`;
}

function toggleDateGroup(dateStr) {
    if (collapsedDates.has(dateStr)) {
        collapsedDates.delete(dateStr);
    } else {
        collapsedDates.add(dateStr);
    }
    renderAziende();
}

function renderAziende() {
    const visible = visibleAziende();
    const incompleteCount = aziende.filter(hasMissingContact).length;
    let info = `${visible.length} aziende`;
    if (filterIncompleteOnly) {
        info += ' senza recapiti completi';
    } else if (incompleteCount) {
        info += ` · ${incompleteCount} da completare`;
    }
    if (selectedGenerazione) {
        info += ' nella generazione selezionata';
    }
    document.getElementById('resultInfo').textContent = info;
    const body = document.getElementById('aziendeBody');
    if (!visible.length) {
        body.innerHTML = filterIncompleteOnly
            ? '<tr><td colspan="6" class="empty">Nessuna azienda senza recapiti completi</td></tr>'
            : '<tr><td colspan="6" class="empty">Nessuna azienda trovata</td></tr>';
        return;
    }

    const groups = new Map();
    for (const a of visible) {
        const dk = a.data_pianificazione || '';
        if (!groups.has(dk)) groups.set(dk, []);
        groups.get(dk).push(a);
    }
    const sortedKeys = [...groups.keys()].sort((a, b) => {
        if (!a && !b) return 0;
        if (!a) return 1;
        if (!b) return -1;
        const isoA = parseDateToISO(a);
        const isoB = parseDateToISO(b);
        return isoA < isoB ? -1 : isoA > isoB ? 1 : 0;
    });
    for (const key of sortedKeys) {
        groups.get(key).sort((a, b) =>
            (a.nominativo || '').localeCompare(b.nominativo || '', 'it', { sensitivity: 'base' })
        );
    }

    let html = '';
    for (const dk of sortedKeys) {
        const items = groups.get(dk);
        const collapsed = collapsedDates.has(dk);
        const incInGroup = items.filter(hasMissingContact).length;
        const label = formatDateGroupHeader(dk);
        html += `
            <tr class="date-group-header-row" onclick="toggleDateGroup('${esc(dk)}')">
                <td colspan="6">
                    <div class="date-group-header">
                        <i class="fas fa-chevron-${collapsed ? 'right' : 'down'}"></i>
                        <span class="date-group-label">${esc(label)}</span>
                        <span class="date-group-count">${items.length} aziende${incInGroup ? ` · ${incInGroup} da completare` : ''}</span>
                    </div>
                </td>
            </tr>
        `;
        if (!collapsed) {
            html += items.map(a => renderAziendaRow(a)).join('');
        }
    }
    body.innerHTML = html;
}

function renderAziendaRow(a) {
    return `
        <tr data-id="${a.id}">
            <td class="select-cell" data-label="Seleziona"><input type="checkbox" class="row-check" data-id="${a.id}" onchange="updateBulkButton()"></td>
            <td data-label="Azienda">
                <div class="azienda-title">${esc(a.nominativo)}</div>
                <div class="azienda-meta">${esc([a.via, a.civico].filter(Boolean).join(' ') || '')}${a.comune ? ' · ' + esc(a.comune) : ''} · ${esc(a.pod || 'POD mancante')}</div>
                <div class="mini file-meta">${esc(a.file_name || '')}</div>
                <button class="research-trigger ${hasMissingContact(a) ? 'needs-contact' : ''}"
                        onclick="openContactResearch(${a.id})">
                    <i class="fas fa-magnifying-glass-location"></i>
                    ${hasMissingContact(a) ? 'Cerca recapiti' : 'Verifica recapiti'}
                </button>
            </td>
            <td data-label="Intervento">
                <strong>${esc(a.data_pianificazione || '-')}</strong>
                <div class="mini">${esc(a.ora_pianificazione || '')}</div>
                <div class="mini">${esc(a.operatore || '')}</div>
            </td>
            <td data-label="Telefono">
                <input class="edit-input" value="${esc(a.telefono || '')}"
                       placeholder="Telefono"
                       onkeydown="saveOnEnter(event)"
                       onblur="saveContact('${esc(a.contact_key)}', this, null)">
            </td>
            <td class="email-cell" data-label="Email / PEC">
                <input class="edit-input" value="${esc(a.email || '')}"
                       placeholder="mail@dominio.it / pec@pec.it"
                       onkeydown="saveOnEnter(event)"
                       onblur="saveContact('${esc(a.contact_key)}', null, this)">
                ${renderEmailBadges(a.email)}
            </td>
            <td class="response-cell" data-label="Invii e risposta">
                <div class="send-status">
                    ${a.invii_ok
                        ? `<span class="badge sent">${a.invii_ok} ${a.invii_ok === 1 ? 'invio' : 'invii'}</span>`
                        : '<span class="mini">Nessun invio registrato</span>'}
                    <div class="mini">${a.last_sent_ts ? 'Ultimo: ' + fmtTs(a.last_sent_ts) : ''}</div>
                </div>
                <label>
                    <input type="checkbox" id="risposta_${a.id}" ${a.risposta ? 'checked' : ''}
                           onchange="saveRisposta(${a.id}, this.checked)">
                    Risposta
                </label>
                <input class="note-input" value="${esc(a.note || '')}" placeholder="Nota risposta"
                       onblur="saveRisposta(${a.id}, document.getElementById('risposta_${a.id}').checked, this.value)">
            </td>
        </tr>
    `;
}

function saveOnEnter(event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    event.target.blur();
}

async function saveContact(contactKey, telInput, emailInput) {
    const row = aziende.find(a => a.contact_key === contactKey);
    if (!row) return;
    const payload = {};
    if (telInput) {
        const val = telInput.value.trim();
        if ((row.telefono || '') === val) return;
        payload.telefono = val;
    }
    if (emailInput) {
        const val = emailInput.value.trim();
        if ((row.email || '') === val) return;
        payload.email = val;
    }
    try {
        const res = await fetch(`/api/lettere/contatti/${encodeURIComponent(contactKey)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Errore salvataggio');
        propagateContactLocally(row.nominativo, row.comune, payload.telefono, payload.email);
        showToast(data.contatto?.sync_filone?.synced ? 'Salvato anche sul filone' : 'Contatto salvato');
        renderAziende();
        updateBulkButton();
        loadStats();
    } catch (e) {
        showToast(e.message, true);
    }
}

async function saveRisposta(id, checked, noteValue) {
    const row = aziende.find(a => Number(a.id) === Number(id));
    if (!row) return;
    const note = noteValue !== undefined ? noteValue : row.note;
    try {
        const res = await fetch(`/api/lettere/aziende/${id}/risposta`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ risposta: checked, note })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Errore risposta');
        row.risposta = checked ? 1 : 0;
        if (noteValue !== undefined) row.note = noteValue;
        showToast('Risposta aggiornata');
        loadStats();
    } catch (e) {
        showToast(e.message, true);
    }
}

function selectedAziende() {
    const ids = [...document.querySelectorAll('.row-check:checked')].map(cb => Number(cb.dataset.id));
    return aziende.filter(a => ids.includes(Number(a.id)));
}

function toggleSelectAll(checked) {
    document.querySelectorAll('.row-check').forEach(cb => { cb.checked = checked; });
    updateBulkButton();
}

function updateBulkButton() {
    const count = selectedAziende().filter(a => splitEmails(a.email).length).length;
    document.getElementById('bulkOpenBtn').disabled = count === 0;
}

function getDefaultAllegati(tipo) {
    const prov = (generazioni[0]?.provincia || '').toUpperCase();
    const base = [...((allegatiInfo.default_provincia || {})[prov] || [])];
    if ((tipo || '').toLowerCase().includes('semidirett') && allegatiInfo.allegato_semidiretta) {
        base.push(allegatiInfo.allegato_semidiretta);
    }
    return base;
}

function openBulkModal() {
    const selected = selectedAziende();
    bulkItems = [];
    const seen = new Set();
    for (const a of selected) {
        for (const e of splitEmails(a.email)) {
            const key = `${a.contact_key}|${e.email.toLowerCase()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            bulkItems.push({
                azienda_id: a.id,
                nome: a.nominativo,
                pod: a.pod,
                email: e.email,
                pec: e.pec,
                allegati: getDefaultAllegati(a.tipo_misuratore)
            });
        }
    }
    if (!bulkItems.length) {
        showToast('Nessun indirizzo email/PEC selezionato', true);
        return;
    }
    renderBulkList();
    chiudiAnteprima();
    document.getElementById('bulkProgress').style.display = 'none';
    document.getElementById('bulkPreviewBtn').disabled = false;
    document.getElementById('bulkModal').classList.add('active');
}

function closeBulkModal() {
    document.getElementById('bulkModal').classList.remove('active');
}

function renderBulkList() {
    const list = document.getElementById('bulkList');
    list.innerHTML = bulkItems.map((b, i) => `
        <div class="bulk-item">
            <input type="checkbox" class="bulk-check" data-idx="${i}" checked>
            <div>
                <div class="bulk-name">${esc(b.nome)} ${b.pec ? '<span class="badge pec">PEC</span>' : '<span class="badge">MAIL</span>'}</div>
                <div class="mini">${esc(b.email)} · ${esc(b.pod || '')}</div>
            </div>
            <div class="mini">${b.allegati.length ? b.allegati.length + ' allegati' : 'no allegati'}</div>
        </div>
    `).join('');
}

function contattoDaAzienda(a, emailDest) {
    const matricola = [a.codice_ante, a.matricola_ante].filter(Boolean).join(' / ');
    return {
        pod: a.pod || '',
        email: emailDest || a.email || '',
        nome: a.nominativo || '',
        indirizzo: a.via || '',
        civico: a.civico || '',
        comune: a.comune || '',
        data_intervento: a.data_pianificazione || '',
        pod_filone: a.pod || '',
        eneltel_filone: a.eneltel || '',
        matricola_filone: matricola,
        tipologia_contatore: a.tipo_misuratore || '',
        nome_societa: a.nominativo || '',
        nome_referente: '',
    };
}

function buildMessageHtml(bodyText) {
    const prov = (generazioni[0]?.provincia || '').toLowerCase();
    const provKey = 'email_' + prov;
    const emailTecnico = templateSettings[provKey] || templateSettings.email_tecnico ||
        (prov === 'pavia' ? templateSettings.email_pavia : templateSettings.email_torino) || '';
    return BulkEmailPreview.buildMessageHtml(bodyText, templateSettings.firma_html || '', emailTecnico);
}

function generaAnteprima() {
    const templateId = document.getElementById('templateSelect').value;
    if (!templateId) {
        showToast('Seleziona un template', true);
        return;
    }
    const tmpl = TEMPLATES[templateId];
    if (!tmpl) {
        showToast('Template non trovato', true);
        return;
    }
    const checkboxes = document.querySelectorAll('.bulk-check:checked');
    if (checkboxes.length === 0) {
        showToast('Nessun contatto selezionato', true);
        return;
    }
    const prov = (generazioni[0]?.provincia || '').toLowerCase();
    const provKey = 'email_' + prov;
    const emailTecnico = templateSettings[provKey] || templateSettings.email_tecnico ||
        (prov === 'pavia' ? templateSettings.email_pavia : templateSettings.email_torino) || '';

    bulkPreviews = [];
    checkboxes.forEach(cb => {
        const idx = Number(cb.dataset.idx);
        const b = bulkItems[idx];
        if (!b) return;
        const a = aziende.find(item => Number(item.id) === Number(b.azienda_id)) || {};
        const c = contattoDaAzienda(a, b.email);
        const fields = {};
        if (tmpl.fields) {
            for (const fId of tmpl.fields) {
                const fd = FIELD_DEFS[fId];
                if (fd && fd.autoFill) {
                    fields[fId] = fd.autoFill(c);
                }
            }
        }
        const bodyText = tmpl.generate(c, fields, emailTecnico);
        const subject = tmpl.generateSubject(c, fields, emailTecnico);

        bulkPreviews.push({
            azienda_id: b.azienda_id,
            pod: b.pod || a.pod || '',
            email: b.email,
            pec: b.pec,
            nome: b.nome || a.nominativo || 'Azienda',
            subject: subject,
            bodyHtml: bodyText,
            contatto: c,
            allegati: [...(b.allegati || [])],
            edited: false,
        });
    });

    if (bulkPreviews.length === 0) {
        showToast('Nessuna anteprima generata', true);
        return;
    }

    renderPreviewCards();
    document.getElementById('bulkSetupSection').style.display = 'none';
    document.getElementById('bulkPreviewSection').style.display = 'block';
    document.getElementById('previewCount').textContent = bulkPreviews.length;
    document.getElementById('bulkPreviewBtn').style.display = 'none';
    document.getElementById('bulkSendBtn').style.display = '';
}

function chiudiAnteprima() {
    document.getElementById('bulkPreviewSection').style.display = 'none';
    document.getElementById('bulkSetupSection').style.display = 'block';
    document.getElementById('bulkPreviewBtn').style.display = '';
    document.getElementById('bulkSendBtn').style.display = 'none';
}

function renderPreviewCards() {
    const container = document.getElementById('bulkPreviewCards');
    const tuttiAllegati = allegatiInfo.allegati_disponibili || [];
    container.innerHTML = bulkPreviews.map((p, i) => {
        const renderedHtml = buildMessageHtml(p.bodyHtml);
        const allegatiHtml = BulkEmailPreview.renderAllegatiList(p.allegati || [], i, tuttiAllegati, 'rimuoviAllegato', 'aggiungiAllegato');
        return `
            <div class="preview-card ${i === 0 ? 'expanded' : ''}" id="previewCard${i}">
                <div class="preview-card-header" onclick="togglePreviewCard(${i})">
                    <i class="fas fa-chevron-right preview-toggle"></i>
                    <span class="preview-dest">
                        <span class="preview-dest-text">${esc(p.nome)} — ${esc(p.email)}</span>
                        ${p.pec ? '<span class="badge pec">PEC</span>' : '<span class="badge">MAIL</span>'}
                    </span>
                    <span class="preview-pod">${esc(p.pod || '')}</span>
                    ${(p.allegati || []).length > 0 ? `<span class="preview-allegati-badge"><i class="fas fa-paperclip"></i> ${p.allegati.length}</span>` : ''}
                    <span class="preview-card-badge edited" id="previewBadge${i}" style="${p.edited ? '' : 'display:none;'}">modificata</span>
                </div>
                <div class="preview-card-body">
                    <div class="preview-field-label">Oggetto:</div>
                    <input type="text" class="preview-subject-input" id="previewSubject${i}"
                           value="${esc(p.subject)}"
                           oninput="onPreviewSubjectEdit(${i})">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-top:10px;">
                        <div class="preview-field-label" style="margin:0;">Anteprima testo:</div>
                        <button type="button" class="preview-edit-toggle" id="previewEditBtn${i}" onclick="toggleEditMode(${i}, event)">
                            <i class="fas fa-pen"></i> Modifica testo
                        </button>
                    </div>
                    <div class="preview-body-rendered" id="previewRendered${i}">${renderedHtml}</div>
                    <div class="rte-container preview-body-editor" id="previewBodyContainer${i}" style="display:none; margin-top: 8px;">
                        ${BulkEmailPreview.buildRteToolbar()}
                        <div class="rte-editor" id="previewBody${i}" contenteditable="true" oninput="onPreviewBodyEdit(${i})">${p.bodyHtml}</div>
                    </div>
                    <div class="preview-allegati-section" id="previewAllegati${i}">
                        <div class="preview-field-label" style="margin-top:10px;">
                            <i class="fas fa-paperclip"></i> Allegati:
                        </div>
                        ${allegatiHtml}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function togglePreviewCard(idx) {
    const card = document.getElementById(`previewCard${idx}`);
    if (card) card.classList.toggle('expanded');
}

function toggleEditMode(idx, event) {
    event.stopPropagation();
    const rendered = document.getElementById(`previewRendered${idx}`);
    const editorContainer = document.getElementById(`previewBodyContainer${idx}`);
    const editor = document.getElementById(`previewBody${idx}`);
    const btn = document.getElementById(`previewEditBtn${idx}`);
    const isEditing = editorContainer.style.display === 'block';
    if (isEditing) {
        bulkPreviews[idx].bodyHtml = editor.innerHTML;
        bulkPreviews[idx].edited = true;
        rendered.innerHTML = buildMessageHtml(editor.innerHTML);
        rendered.style.display = '';
        editorContainer.style.display = 'none';
        btn.innerHTML = '<i class="fas fa-pen"></i> Modifica testo';
        document.getElementById(`previewBadge${idx}`).style.display = '';
    } else {
        rendered.style.display = 'none';
        editorContainer.style.display = 'block';
        editor.focus();
        btn.innerHTML = '<i class="fas fa-eye"></i> Vedi anteprima';
    }
}

function onPreviewSubjectEdit(idx) {
    const inp = document.getElementById(`previewSubject${idx}`);
    if (inp) {
        bulkPreviews[idx].subject = inp.value;
        bulkPreviews[idx].edited = true;
        document.getElementById(`previewBadge${idx}`).style.display = '';
    }
}

function onPreviewBodyEdit(idx) {
    const editor = document.getElementById(`previewBody${idx}`);
    if (editor) {
        bulkPreviews[idx].bodyHtml = editor.innerHTML;
        bulkPreviews[idx].edited = true;
        document.getElementById(`previewBadge${idx}`).style.display = '';
    }
}

function rimuoviAllegato(previewIdx, allegatoIdx) {
    bulkPreviews[previewIdx].allegati.splice(allegatoIdx, 1);
    bulkPreviews[previewIdx].edited = true;
    document.getElementById(`previewBadge${previewIdx}`).style.display = '';
    const section = document.getElementById(`previewAllegati${previewIdx}`);
    const tuttiAllegati = allegatiInfo.allegati_disponibili || [];
    section.innerHTML = `
        <div class="preview-field-label" style="margin-top:10px;">
            <i class="fas fa-paperclip"></i> Allegati:
        </div>
    ` + BulkEmailPreview.renderAllegatiList(bulkPreviews[previewIdx].allegati, previewIdx, tuttiAllegati, 'rimuoviAllegato', 'aggiungiAllegato');
    aggiornaAllegatiBadge(previewIdx);
}

function aggiungiAllegato(previewIdx) {
    const select = document.getElementById(`addAllegatoSelect${previewIdx}`);
    const nome = select ? select.value : '';
    if (!nome) return;
    if (!bulkPreviews[previewIdx].allegati.includes(nome)) {
        bulkPreviews[previewIdx].allegati.push(nome);
        bulkPreviews[previewIdx].edited = true;
        document.getElementById(`previewBadge${previewIdx}`).style.display = '';
    }
    const section = document.getElementById(`previewAllegati${previewIdx}`);
    const tuttiAllegati = allegatiInfo.allegati_disponibili || [];
    section.innerHTML = `
        <div class="preview-field-label" style="margin-top:10px;">
            <i class="fas fa-paperclip"></i> Allegati:
        </div>
    ` + BulkEmailPreview.renderAllegatiList(bulkPreviews[previewIdx].allegati, previewIdx, tuttiAllegati, 'rimuoviAllegato', 'aggiungiAllegato');
    aggiornaAllegatiBadge(previewIdx);
}

function aggiornaAllegatiBadge(previewIdx) {
    const headerBadge = document.querySelector(`#previewCard${previewIdx} .preview-allegati-badge`);
    const count = (bulkPreviews[previewIdx].allegati || []).length;
    if (headerBadge) {
        headerBadge.innerHTML = `<i class="fas fa-paperclip"></i> ${count}`;
        headerBadge.style.display = count > 0 ? '' : 'none';
    }
}

async function sendBulk() {
    const templateId = document.getElementById('templateSelect').value;
    if (!templateId) {
        showToast('Seleziona un template', true);
        return;
    }
    if (!bulkPreviews || !bulkPreviews.length) {
        showToast('Nessuna email da inviare', true);
        return;
    }
    if (!confirm(`Inviare ${bulkPreviews.length} email/PEC?\nPausa di ${PAUSA_TRA_EMAIL_SEC}s tra un invio e il successivo.`)) {
        return;
    }

    bulkAbort = false;
    const progress = document.getElementById('bulkProgress');
    const fill = document.getElementById('progressFill');
    const text = document.getElementById('progressText');
    const log = document.getElementById('bulkLog');
    const sendBtn = document.getElementById('bulkSendBtn');
    progress.style.display = '';
    log.innerHTML = '';
    fill.style.width = '0%';
    sendBtn.innerHTML = '<i class="fas fa-stop"></i> Interrompi';
    sendBtn.onclick = () => { bulkAbort = true; };

    let ok = 0;
    let ko = 0;
    for (let i = 0; i < bulkPreviews.length; i++) {
        if (bulkAbort) {
            log.innerHTML += `<div class="log-err">Invio interrotto dopo ${ok} invii riusciti</div>`;
            break;
        }
        const item = bulkPreviews[i];
        text.textContent = `${i + 1} / ${bulkPreviews.length}`;
        fill.style.width = `${Math.round(((i + 1) / bulkPreviews.length) * 100)}%`;

        const subjInput = document.getElementById(`previewSubject${i}`);
        const currentSubject = subjInput ? subjInput.value : item.subject;
        const editorEl = document.getElementById(`previewBody${i}`);
        const currentBody = (editorEl && document.getElementById(`previewBodyContainer${i}`)?.style.display === 'block')
            ? editorEl.innerHTML
            : item.bodyHtml;

        try {
            const res = await fetch('/api/lettere/invia-email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    azienda_id: item.azienda_id,
                    email: item.email,
                    template_id: templateId,
                    subject_override: item.edited ? currentSubject : undefined,
                    body_override: item.edited ? currentBody : undefined,
                    allegati: item.allegati,
                })
            });
            const data = await res.json();
            if (data.ok) {
                ok++;
                log.innerHTML += `<div class="log-ok">OK ${esc(item.email)}</div>`;
            } else {
                ko++;
                log.innerHTML += `<div class="log-err">Errore ${esc(item.email)}: ${esc(data.errore || data.error || '')}</div>`;
            }
        } catch (e) {
            ko++;
            log.innerHTML += `<div class="log-err">Errore ${esc(item.email)}: rete</div>`;
        }
        log.scrollTop = log.scrollHeight;
        if (i < bulkPreviews.length - 1 && !bulkAbort) {
            await new Promise(r => setTimeout(r, PAUSA_TRA_EMAIL_SEC * 1000));
        }
    }

    sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Invia selezionate';
    sendBtn.onclick = sendBulk;
    text.textContent = `${ok} inviate, ${ko} errori`;
    showToast(`${ok} invii completati${ko ? ', ' + ko + ' errori' : ''}`, Boolean(ko));
    await loadAziende();
    await loadStats();
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('searchInput').addEventListener('keydown', e => {
        if (e.key === 'Enter') loadAziende();
    });
    const autoOpenEl = document.getElementById('researchAutoOpen');
    if (autoOpenEl) {
        const saved = localStorage.getItem(RESEARCH_AUTO_OPEN_KEY);
        if (saved !== null) {
            autoOpenEl.checked = saved === '1';
        }
        autoOpenEl.addEventListener('change', () => {
            localStorage.setItem(RESEARCH_AUTO_OPEN_KEY, autoOpenEl.checked ? '1' : '0');
        });
    }
    document.addEventListener('keydown', e => {
        if (e.key !== 'Escape') return;
        if (document.getElementById('researchModal').classList.contains('active')) {
            closeContactResearch();
        } else if (document.getElementById('bulkModal').classList.contains('active')) {
            closeBulkModal();
        }
    });
    loadAll().catch(e => showToast(e.message, true));
});
