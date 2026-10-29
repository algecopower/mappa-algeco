// -*- coding: utf-8 -*-
let generazioni = [];
let aziende = [];
let privati = [];
let selectedGenerazione = '';
let currentMainView = 'aziende';
let completedAziende = JSON.parse(localStorage.getItem('lettere_aziende_completed') || '{}');
let completedPrivati = JSON.parse(localStorage.getItem('lettere_privati_completed') || '{}');

function isAziendaCompleted(a) {
    if (!a) return false;
    return Boolean(a.risposta || completedAziende[a.id] || (a.pod && completedAziende[a.pod]));
}

let noteStatesPrivati = {};
let erratiPrivati = JSON.parse(localStorage.getItem('lettere_privati_errati') || '{}');
let privatiController = null;
let currentSmsSettings = null;
let bulkSmsAbortato = false;
let bulkSmsContacts = [];
let activeModalPrivato = null;
let selectedPrivatiTmplId = '';

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

const PAUSA_TRA_EMAIL_SEC = 5;
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
    await Promise.all([loadAziende(), loadPrivati()]);
}

async function loadGenerazioni() {
    const res = await fetch('/api/lettere/generazioni');
    const data = await res.json();
    if (!data.success) throw new Error(data.error || 'Errore generazioni');
    if (data.provincia) {
        window.provincia = data.provincia;
    }
    generazioni = data.generazioni || [];
    renderGenerazioni();
}

async function loadTemplates() {
    const data = await BulkEmailPreview.loadCentralizedTemplates('/api/distinte/templates');
    templateSettings = data.settings || {};
    window.templateSettings = templateSettings;
    FIELD_DEFS = data.fieldDefs || {};
    TEMPLATES = data.templates || {};

    const select = document.getElementById('templateSelect');
    if (select) {
        select.innerHTML = '';
        for (const [id, tmpl] of Object.entries(TEMPLATES)) {
            if (tmpl.type !== 'email') continue;
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = tmpl.label || id;
            select.appendChild(opt);
        }
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
    try {
        const res = await fetch('/api/lettere/statistiche');
        const data = await res.json();
        const s = data.lettere || {};
        const statsBox = document.getElementById('statsBox');
        if (statsBox) {
            statsBox.innerHTML = `
                <div class="kpi" id="kpiGenerazioni"><span>${s.generazioni || 0}</span><small>Generazioni</small></div>
                <div class="kpi" id="kpiAziende"><span>${s.aziende || 0}</span><small>Aziende</small></div>
                <div class="kpi" id="kpiPrivati"><span>${s.privati || 0}</span><small>Utenti Privati</small></div>
                <div class="kpi" id="kpiEmailPec"><span>${s.copertura_contatti || 0}%</span><small>Con email/PEC</small></div>
                <div class="kpi" id="kpiFeedback"><span>${s.tasso_feedback || 0}%</span><small>Feedback</small></div>
            `;
        }
    } catch (e) {
        console.error('Errore caricamento statistiche:', e);
    }
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
    await Promise.all([loadAziende(), loadPrivati()]);
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
        aziende.forEach(a => {
            if (a.risposta) {
                completedAziende[a.id] = true;
                if (a.pod) completedAziende[a.pod] = true;
            }
        });
        const badgeAz = document.getElementById('badgeCountAziende');
        if (badgeAz) badgeAz.textContent = aziende.length;
        renderAziende();
        updateBulkButton();
    } catch (e) {
        if (e.name !== 'AbortError') {
            showToast(e.message || 'Errore caricamento aziende', true);
        }
    }
}

function renderEmailBadges(emailField) {
    const raw = (emailField || '').trim();
    if (!raw) return '<div class="mini">Nessun indirizzo</div>';
    const emails = splitEmails(raw);
    if (!emails.length) {
        return `<div class="mini" style="color:var(--text-muted);font-style:italic;">${esc(raw)}</div>`;
    }
    return `<div class="badges">${emails.map(e =>
        `<span class="badge ${e.pec ? 'pec' : ''}">${e.pec ? 'PEC' : 'MAIL'}</span>`
    ).join('')}</div>`;
}

function hasMissingContact(a) {
    const hasTel = Boolean((a.telefono || '').trim());
    const hasMail = Boolean((a.email || '').trim());
    return !hasTel || !hasMail;
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
        !(row.email || '').trim() ? 'email/PEC' : ''
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

function setResearchField(fieldId, val) {
    const el = document.getElementById(fieldId);
    if (el) {
        el.value = val;
        el.focus();
    }
}

function setBothNotFound() {
    setResearchField('researchPhone', 'non trovato');
    setResearchField('researchEmail', 'non trovata');
}

function autoOpenResearchesEnabled() {
    const el = document.getElementById('researchAutoOpen');
    return !el || el.checked;
}

function getPrimaryResearchLinks(row) {
    const googleLinks = buildResearchGroups(row)[0].links;
    const missingPhone = !(row.telefono || '').trim();
    const missingEmail = !(row.email || '').trim();
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

    // Trova la posizione del contatto corrente nella coda degli incompleti PRIMA dell'aggiornamento
    const queueBefore = researchQueue();
    const currentIndex = queueBefore.findIndex(a => (a.nominativo || '').trim().toUpperCase() === currentNom);

    // Aggiorna subito lo stato locale prima di cercare il prossimo contatto incompleto
    row.telefono = telefono;
    row.email = email;
    propagateContactLocally(row.nominativo, row.comune, telefono, email);

    // Calcola il prossimo contatto incompleto da mostrare
    let nextRow = null;
    if (openNext) {
        const queueAfter = researchQueue();
        const stillIncomplete = hasMissingContact(row);

        if (stillIncomplete) {
            // Il contatto è ancora incompleto (es. inserito solo telefono o solo email):
            // avanziamo al contatto successivo nella coda per non rimanere bloccati sullo stesso
            if (currentIndex >= 0 && currentIndex + 1 < queueBefore.length) {
                nextRow = queueBefore[currentIndex + 1];
            } else if (queueBefore.length > 1) {
                // Se eravamo all'ultimo elemento, ripartiamo dal primo
                nextRow = queueBefore[0];
            }
        } else {
            // Il contatto è diventato completo (telefono ed email presenti):
            // prendiamo il nuovo elemento che si trova alla posizione currentIndex nella coda aggiornata
            if (queueAfter.length > 0) {
                const targetIdx = (currentIndex >= 0 && currentIndex < queueAfter.length) ? currentIndex : 0;
                nextRow = queueAfter[targetIdx];
            }
        }
    }

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

let filterRecapitoVal = 'tutti';

function setFilterRecapito(val) {
    filterRecapitoVal = val || 'tutti';
    renderAziende();
}

function visibleAziende() {
    return aziende.filter(a => {
        const emails = splitEmails(a.email);
        const hasPec = emails.some(e => e.pec);
        const hasMail = emails.some(e => !e.pec);
        const tipo = (a.tipo_misuratore || '').toLowerCase();
        const note = (a.note_appuntamento || a.note || '').trim();

        if (filterRecapitoVal === 'incompleti' && !hasMissingContact(a)) return false;
        if (filterRecapitoVal === 'completati' && !isAziendaCompleted(a)) return false;
        if (filterRecapitoVal === 'da_fare' && isAziendaCompleted(a)) return false;
        if (filterRecapitoVal === 'solo_mail' && (!hasMail || hasPec)) return false;
        if (filterRecapitoVal === 'solo_pec' && (!hasPec || hasMail)) return false;
        if (filterRecapitoVal === 'monofase' && !tipo.includes('mono')) return false;
        if (filterRecapitoVal === 'trifase' && !(tipo.includes('tri') && !tipo.includes('semidirett'))) return false;
        if (filterRecapitoVal === 'semidiretta' && !tipo.includes('semidirett')) return false;
        if (filterRecapitoVal === 'con_note' && !note) return false;
        if (filterRecapitoVal === 'senza_note' && note) return false;
        if (filterRecapitoVal === 'con_email' && emails.length === 0) return false;
        if (filterRecapitoVal === 'senza_email' && emails.length > 0) return false;
        if (filterRecapitoVal === 'non_inviati' && (a.invii_ok || 0) > 0) return false;
        if (filterRecapitoVal === 'date_modificate' && !checkCambioData(a.data_pianificazione, a.data_pian_filone).cambiato) return false;

        return true;
    });
}

function toggleIncompleteFilter(checked) {
    filterRecapitoVal = checked ? 'incompleti' : 'tutti';
    const sel = document.getElementById('filterRecapito');
    if (sel) sel.value = filterRecapitoVal;
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

function parseDateToYmd(str) {
    if (!str) return null;
    const s = String(str).trim();
    if (!s) return null;
    if (s.toUpperCase().includes('ANNULLAT')) return 'ANNULLATO';
    const dmy = s.match(/^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})$/);
    if (dmy) {
        return `${dmy[3]}-${dmy[2].padStart(2, '0')}-${dmy[1].padStart(2, '0')}`;
    }
    const ymd = s.match(/^(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})$/);
    if (ymd) {
        return `${ymd[1]}-${ymd[2].padStart(2, '0')}-${ymd[3].padStart(2, '0')}`;
    }
    return s;
}

function checkCambioData(dataLettera, dataFilone) {
    if (!dataFilone || !String(dataFilone).trim()) return { cambiato: false };
    const sFilone = String(dataFilone).trim();
    const sLettera = String(dataLettera || '').trim();

    if (sFilone.toUpperCase().includes('ANNULLAT')) {
        return {
            cambiato: true,
            isAnnullato: true,
            dataLettera: sLettera,
            dataAttuale: 'ANNULLATO'
        };
    }

    if (!sLettera) return { cambiato: false };

    const normLettera = parseDateToYmd(sLettera);
    const normFilone = parseDateToYmd(sFilone);

    if (normFilone && normLettera && normFilone !== normLettera) {
        return {
            cambiato: true,
            isAnnullato: false,
            dataLettera: sLettera,
            dataAttuale: sFilone
        };
    }
    return { cambiato: false };
}

function renderDataInterventoHtml(dataLettera, dataFilone, ora) {
    const info = checkCambioData(dataLettera, dataFilone);
    const oraHtml = ora ? `<div class="meta-sub"><i class="fas fa-clock" style="font-size:0.72rem;"></i> ${esc(ora)}</div>` : '';

    if (!info.cambiato) {
        const d = dataLettera || dataFilone || '-';
        return `
            <div style="font-size:0.82rem;font-weight:600;"><i class="fas fa-calendar" style="font-size:0.75rem;color:var(--text-muted);"></i> ${esc(d)}</div>
            ${oraHtml}
        `;
    }

    if (info.isAnnullato) {
        return `
            <div style="margin-bottom:2px;"><span class="badge badge-annullato" title="Intervento contrassegnato come ANNULLATO nel database locale"><i class="fas fa-ban"></i> ANNULLATO</span></div>
            <div class="meta-sub" style="font-size:0.72rem;color:var(--text-muted);">Lettera: <del>${esc(dataLettera)}</del></div>
            ${oraHtml}
        `;
    }

    return `
        <div style="font-size:0.82rem;font-weight:700;color:var(--text-primary);">
            <i class="fas fa-calendar-check" style="color:#f59e0b;font-size:0.75rem;"></i> ${esc(info.dataAttuale)}
        </div>
        <div style="margin-top:2px;">
            <span class="badge badge-cambio-data" title="Data modificata nel gestionale: da ${esc(dataLettera)} a ${esc(info.dataAttuale)}">
                <i class="fas fa-history"></i> Lettera: <del>${esc(dataLettera)}</del>
            </span>
        </div>
        ${oraHtml}
    `;
}

function renderDataInterventoCard(c) {
    const dataLettera = c.data_lettera || c.data_intervento || '';
    const dataFilone = c.data_pian_filone || '';
    const ora = c.ora_intervento || '';
    const oraSuffix = ora ? ` — ${esc(ora)}` : '';
    const info = checkCambioData(dataLettera, dataFilone);

    if (!info.cambiato) {
        return `<div class="date"><i class="fas fa-calendar"></i> ${esc(dataLettera || dataFilone || '')} ${oraSuffix}</div>`;
    }

    if (info.isAnnullato) {
        return `
            <div class="date" style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;">
                <span class="badge badge-annullato" title="Intervento contrassegnato come ANNULLATO nel database locale"><i class="fas fa-ban"></i> ANNULLATO</span>
                <span style="font-size:0.75rem;color:var(--text-muted);">Lettera: <del>${esc(dataLettera)}</del></span>
                ${ora ? `<span style="font-size:0.75rem;color:var(--text-muted);">${oraSuffix}</span>` : ''}
            </div>
        `;
    }

    return `
        <div class="date" style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;">
            <i class="fas fa-calendar-check" style="color:#f59e0b;"></i>
            <strong>${esc(info.dataAttuale)}</strong>
            ${ora ? `<span style="font-size:0.78rem;">${oraSuffix}</span>` : ''}
            <span class="badge badge-cambio-data" title="Data modificata nel gestionale: da ${esc(dataLettera)} a ${esc(info.dataAttuale)}">
                <i class="fas fa-history"></i> Lettera: <del>${esc(dataLettera)}</del>
            </span>
        </div>
    `;
}

function groupContactsByDate(items, getDateFn, getNameFn) {
    const groups = new Map();
    for (const it of items) {
        const dk = (getDateFn(it) || '').trim() || 'Senza data';
        if (!groups.has(dk)) groups.set(dk, []);
        groups.get(dk).push(it);
    }
    const sortedKeys = [...groups.keys()].sort((a, b) => {
        if (a === 'Senza data' && b === 'Senza data') return 0;
        if (a === 'Senza data') return 1;
        if (b === 'Senza data') return -1;
        const isoA = parseDateToISO(a);
        const isoB = parseDateToISO(b);
        return isoA < isoB ? -1 : isoA > isoB ? 1 : 0;
    });
    for (const key of sortedKeys) {
        groups.get(key).sort((a, b) =>
            (getNameFn(a) || '').localeCompare(getNameFn(b) || '', 'it', { sensitivity: 'base' })
        );
    }
    return { groups, sortedKeys };
}

function formatDateGroupHeader(dateStr) {
    if (!dateStr || dateStr === 'Senza data') return 'Data non specificata';
    try {
        let d, m, y;
        if (dateStr.includes('/')) {
            const parts = dateStr.split('/').map(Number);
            d = parts[0]; m = parts[1]; y = parts[2];
        } else if (dateStr.includes('-')) {
            const parts = dateStr.split('-').map(Number);
            y = parts[0]; m = parts[1]; d = parts[2];
        } else {
            return dateStr;
        }
        if (!d || !m || !y || isNaN(d) || isNaN(m) || isNaN(y)) return dateStr;
        const dt = new Date(y, m - 1, d);
        if (isNaN(dt.getTime())) return dateStr;
        return `${GIORNI_IT[dt.getDay()]} ${d} ${MESI_IT_LOWER[m - 1]} ${y}`;
    } catch {
        return dateStr;
    }
}

function toggleDateGroup(dateStr) {
    if (collapsedDates.has(dateStr)) {
        collapsedDates.delete(dateStr);
    } else {
        collapsedDates.add(dateStr);
    }
    if (currentMainView === 'privati') {
        renderPrivati();
    } else {
        renderAziende();
    }
}

let aziendeViewMode = localStorage.getItem('lettere_aziende_view_mode') || 'table';

function setAziendeViewMode(mode) {
    aziendeViewMode = mode;
    localStorage.setItem('lettere_aziende_view_mode', mode);
    document.getElementById('btnAziendeViewTable')?.classList.toggle('active', mode === 'table');
    document.getElementById('btnAziendeViewCards')?.classList.toggle('active', mode === 'cards');
    renderAziende();
}

function renderAziende() {
    const tableWrap = document.getElementById('aziendeTableWrap');
    const grid = document.getElementById('aziendeGrid');
    document.getElementById('btnAziendeViewTable')?.classList.toggle('active', aziendeViewMode === 'table');
    document.getElementById('btnAziendeViewCards')?.classList.toggle('active', aziendeViewMode === 'cards');

    const visible = visibleAziende();
    const incompleteCount = aziende.filter(hasMissingContact).length;
    const daFareCount = aziende.filter(a => !isAziendaCompleted(a)).length;
    let info = `${visible.length} aziende`;
    if (filterRecapitoVal === 'incompleti') {
        info += ' senza recapiti completi';
    } else if (filterRecapitoVal === 'da_fare') {
        info += ` · ${daFareCount} da fare`;
    } else if (filterRecapitoVal === 'completati') {
        info += ` · ${aziende.length - daFareCount} completate`;
    } else {
        if (daFareCount) info += ` · ${daFareCount} da fare`;
        if (incompleteCount) info += ` (${incompleteCount} recapiti mancanti)`;
    }
    if (selectedGenerazione) {
        info += ' nella generazione selezionata';
    }
    const resInfo = document.getElementById('resultInfo');
    if (resInfo) resInfo.textContent = info;

    const { groups, sortedKeys } = groupContactsByDate(
        visible,
        a => a.data_pianificazione,
        a => a.nominativo
    );

    if (aziendeViewMode === 'table') {
        if (grid) grid.style.display = 'none';
        if (tableWrap) tableWrap.style.display = 'block';

        const body = document.getElementById('aziendeBody');
        if (!body) return;

        if (!visible.length) {
            body.innerHTML = '<tr><td colspan="6" class="empty">Nessuna azienda trovata per il filtro selezionato</td></tr>';
            return;
        }

        let html = '';
        for (const dk of sortedKeys) {
            const items = groups.get(dk);
            const collapsed = collapsedDates.has(dk);
            const daFareInGroup = items.filter(a => !isAziendaCompleted(a)).length;
            const incInGroup = items.filter(hasMissingContact).length;
            const label = formatDateGroupHeader(dk);
            html += `
                <tr class="date-group-header-row" onclick="toggleDateGroup('${esc(dk)}')">
                    <td colspan="6">
                        <div class="date-group-header">
                            <i class="fas fa-chevron-${collapsed ? 'right' : 'down'}"></i>
                            <span class="date-group-label">${esc(label)}</span>
                            <span class="date-group-count">${items.length} aziende${daFareInGroup ? ` · ${daFareInGroup} da fare` : ' · completate'}${incInGroup ? ` (${incInGroup} inc.)` : ''}</span>
                        </div>
                    </td>
                </tr>
            `;
            if (!collapsed) {
                html += items.map(a => renderAziendaRow(a)).join('');
            }
        }
        body.innerHTML = html;
    } else {
        if (tableWrap) tableWrap.style.display = 'none';
        if (grid) {
            grid.style.display = 'block';

            if (!visible.length) {
                grid.innerHTML = `
                    <div class="empty-state" style="padding:40px 20px;text-align:center;color:var(--text-muted);">
                        <i class="fas fa-filter" style="font-size:2rem;margin-bottom:10px;display:block;"></i>
                        <h3>Nessuna azienda trovata</h3>
                        <p>Prova a modificare i filtri o il termine di ricerca</p>
                    </div>
                `;
                return;
            }

            let cardsHtml = '';
            for (const dk of sortedKeys) {
                const items = groups.get(dk);
                const collapsed = collapsedDates.has(dk);
                const daFareInGroup = items.filter(a => !isAziendaCompleted(a)).length;
                const incInGroup = items.filter(hasMissingContact).length;
                const label = formatDateGroupHeader(dk);
                cardsHtml += `
                    <div class="date-group-card-block">
                        <div class="date-group-header-card" onclick="toggleDateGroup('${esc(dk)}')">
                            <i class="fas fa-chevron-${collapsed ? 'right' : 'down'}"></i>
                            <span class="date-group-label">${esc(label)}</span>
                            <span class="date-group-count">${items.length} aziende${daFareInGroup ? ` · ${daFareInGroup} da fare` : ' · completate'}${incInGroup ? ` (${incInGroup} inc.)` : ''}</span>
                        </div>
                        ${!collapsed ? `
                            <div class="contacts-grid">
                                ${items.map(a => renderAziendaCard(a)).join('')}
                            </div>
                        ` : ''}
                    </div>
                `;
            }
            grid.innerHTML = cardsHtml;
        }
    }
}

let selectedAziendeIds = new Set();

function renderSearchShortcuts(a) {
    const missing = hasMissingContact(a);
    return `
        <button type="button" class="research-trigger ${missing ? 'needs-contact' : ''}"
                onclick="openContactResearch(${a.id})" title="Cerca recapiti online (Google, INI-PEC, Registro Imprese)">
            <i class="fas fa-magnifying-glass"></i> Cerca recapiti
        </button>
    `;
}

function renderAziendaRow(a) {
    const checked = selectedAziendeIds.has(Number(a.id));
    const isCompleted = isAziendaCompleted(a);
    const emails = splitEmails(a.email);
    const missing = hasMissingContact(a);
    const note = (a.note || a.note_filone || '').trim();
    const rowClass = [
        isCompleted ? 'completed' : '',
        missing ? 'incomplete-row' : '',
        checked ? 'selected' : '',
        isCompleted ? 'row-risposta' : '',
        a.invii_ok > 0 ? 'row-inviata' : ''
    ].filter(Boolean).join(' ');

    const inviiLabel = a.invii_ok > 0
        ? `<span class="badge inviata" title="Inviata ${fmtTs(a.last_sent_ts)}"><i class="fas fa-check"></i> Inviata (${a.invii_ok})</span>`
        : '<span class="badge non-inviata">Non inviata</span>';

    return `
        <tr class="${rowClass}" data-id="${a.id}">
            <td class="checkbox-cell">
                <input type="checkbox" class="row-check" data-id="${a.id}" ${checked ? 'checked' : ''} onchange="onRowCheckChange(this)">
            </td>
            <td class="azienda-cell">
                <div class="azienda-name">
                    ${esc(a.nominativo || 'Senza nominativo')}
                    ${a.nominativo ? `
                        <button type="button" class="btn-mini" onclick="copiaNominativoAzienda(${a.id}, event)" title="Copia nominativo" style="background:transparent;border:none;color:var(--text-muted);cursor:pointer;padding:0 3px;">
                            <i class="fas fa-copy"></i>
                        </button>
                    ` : ''}
                    ${missing ? '<span class="badge da-completare" title="Recapiti mancanti">Da completare</span>' : ''}
                </div>
                <div class="azienda-sub">
                    <span><b>${esc(a.pod || '')}</b></span> ·
                    <span>${esc(a.comune || '')}</span>
                    ${a.via ? `· <span>${esc(a.via)} ${esc(a.civico || '')}</span>` : ''}
                </div>
                ${renderSearchShortcuts(a)}
            </td>
            <td class="intervento-cell">
                ${renderDataInterventoHtml(a.data_pianificazione, a.data_pian_filone, (a.ora_pianificazione || '') + (a.operatore ? ' · ' + a.operatore : ''))}
            </td>
            <td class="tel-cell" data-label="Telefono">
                <input class="tel-input edit-input ${a.telefono ? 'has-val' : ''}" value="${esc(a.telefono || '')}" placeholder="Tel..."
                       onkeydown="if(event.key==='Enter'){event.preventDefault(); saveInlineContact(${a.id}, 'telefono', this.value);}"
                       onblur="saveInlineContact(${a.id}, 'telefono', this.value)">
            </td>
            <td class="email-cell" data-label="Email / PEC">
                <input class="email-input edit-input ${a.email ? 'has-val' : ''}" value="${esc(a.email || '')}"
                       placeholder="mail@dominio.it / pec@pec.it"
                       onkeydown="if(event.key==='Enter'){event.preventDefault(); saveInlineContact(${a.id}, 'email', this.value);}"
                       onblur="saveInlineContact(${a.id}, 'email', this.value)">
                <div class="email-badges">
                    ${emails.length ? emails.map(e => `<span class="badge ${e.pec ? 'pec' : ''}">${e.pec ? 'PEC' : 'MAIL'}</span>`).join('') : ((a.email || '').trim() ? `<span class="badge" style="background:var(--surface-raised, #334155);color:var(--text-muted);font-size:0.65rem;">${esc(a.email)}</span>` : '')}
                </div>
            </td>
            <td class="invii-cell">
                <div class="invii-status">${inviiLabel}</div>
                <div class="risposta-row">
                    <label class="risposta-label" title="Contrassegna come completata / risposta ricevuta">
                        <input type="checkbox" class="risposta-check" ${isCompleted ? 'checked' : ''}
                                onchange="toggleCompletedAzienda(${a.id})">
                        <span>Fatto</span>
                    </label>
                    <div style="display:flex;align-items:center;gap:4px;flex:1;position:relative;">
                        <input class="note-input ${note ? 'has-note' : ''}" id="noteTable_azienda_${a.id}"
                               value="${esc(note)}" placeholder="Nota..."
                               onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaNotaAzienda(${a.id}, this.value);}"
                               onblur="salvaNotaAzienda(${a.id}, this.value)">
                        <div class="btn-add-note" onclick="toggleNoteDropdownAzienda(${a.id})" style="padding:4px 7px;font-size:0.75rem;" title="Aggiungi nota rapida">
                            <i class="fas fa-plus"></i>
                            <div class="note-dropdown" id="noteDropdown_azienda_${a.id}">
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'mandata mail')"><i class="fas fa-envelope" style="color: #6366f1"></i> Mandata mail</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'mandato wa')"><i class="fab fa-whatsapp" style="color: #25d366"></i> Mandato WA</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'mandato sms')"><i class="fas fa-sms" style="color: #10b981"></i> Mandato SMS</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'nr')"><i class="fas fa-phone-slash" style="color: #ef4444"></i> NR</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'chiamare')"><i class="fas fa-phone" style="color: #3b82f6"></i> Chiamare</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'confermato')"><i class="fas fa-check-circle" style="color: #10b981"></i> Confermato</div>
                            </div>
                        </div>
                    </div>
                </div>
                <div style="margin-top:5px;">
                    <button type="button" class="btn-mini" onclick="apriAziendaMessageModal(${a.id})" title="Apri Template Messaggio (Mail / WA / SMS)" style="padding:3px 8px;font-size:0.75rem;border-radius:4px;background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);cursor:pointer;display:inline-flex;align-items:center;gap:4px;">
                        <i class="fas fa-paper-plane"></i> Template
                    </button>
                </div>
            </td>
        </tr>
    `;
}

function toggleNoteDropdownAzienda(id) {
    const el = document.getElementById(`noteDropdown_azienda_${id}`) || document.getElementById(`noteDropdown_card_azienda_${id}`);
    if (el) {
        const isOpen = el.classList.contains('show');
        document.querySelectorAll('.note-dropdown.show').forEach(d => d.classList.remove('show'));
        if (!isOpen) el.classList.add('show');
    }
}

async function salvaNotaAzienda(id, nota) {
    const a = aziende.find(x => Number(x.id) === Number(id));
    if (!a) return;
    const cleanNota = String(nota || '').trim();
    a.note = cleanNota;

    const inputTable = document.getElementById(`noteTable_azienda_${id}`);
    if (inputTable) {
        inputTable.value = cleanNota;
        inputTable.classList.toggle('has-note', Boolean(cleanNota));
    }
    const inputCard = document.getElementById(`noteDisplay_azienda_${id}`);
    if (inputCard) {
        inputCard.value = cleanNota;
        inputCard.classList.toggle('has-note', Boolean(cleanNota));
    }

    try {
        const res = await fetch(`/api/lettere/aziende/${id}/risposta`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ note: cleanNota, lcl: a.lcl || '' })
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Errore salvataggio nota');
        showToast('Nota salvata');
    } catch (e) {
        showToast(e.message, true);
    }
}

async function aggiungiNotaAzienda(id, testo, opzioni) {
    opzioni = opzioni || {};
    document.querySelectorAll('.note-dropdown.show').forEach(d => d.classList.remove('show'));
    const a = aziende.find(x => Number(x.id) === Number(id));
    if (!a) return;
    const existing = (a.note || a.note_filone || a.note_appuntamento || '').trim();

    const oggi = new Date();
    const gg = String(oggi.getDate()).padStart(2, '0');
    const mm = String(oggi.getMonth() + 1).padStart(2, '0');
    const prefisso = `${gg}/${mm}`;

    const tLower = (testo || '').toLowerCase().trim();
    const isMail = tLower === 'mandata mail' || tLower === 'mail';
    const isWa = tLower === 'mandato wa' || tLower === 'mandato msg wa' || tLower === 'mandato whatsapp' || tLower === 'wa';

    let testoNota = testo;
    if (tLower === 'chiamare') {
        testoNota = 'chiamare';
    } else if (isMail || isWa) {
        const azione = isMail ? 'mandata mail' : 'mandato wa';
        const dataTarget = opzioni.dataIntervento || a.data_intervento || a.data_pian_filone || a.data_lettera || '';
        const dataTargetBreve = formatDataBreve(dataTarget);
        testoNota = dataTargetBreve ? `${prefisso} ${azione} per ${dataTargetBreve}` : `${prefisso} ${azione}`;
    } else if (!testo.startsWith(prefisso) && !testo.match(/^\d{1,2}\/\d{1,2}/)) {
        testoNota = `${prefisso} ${testo}`;
    }

    if (existing && existing.includes(testoNota)) {
        showToast('Nota già presente');
        return;
    }
    const nuovaNota = existing ? `${testoNota} // ${existing}` : testoNota;
    await salvaNotaAzienda(id, nuovaNota);
}

async function toggleCompletedAzienda(id) {
    const a = aziende.find(x => Number(x.id) === Number(id));
    if (!a) return;
    const nextState = !isAziendaCompleted(a);
    a.risposta = nextState ? 1 : 0;
    completedAziende[a.id] = nextState;
    if (a.pod) completedAziende[a.pod] = nextState;
    localStorage.setItem('lettere_aziende_completed', JSON.stringify(completedAziende));

    renderAziende();
    loadStats();

    try {
        const res = await fetch(`/api/lettere/aziende/${id}/risposta`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ risposta: nextState, lcl: a.lcl || '' })
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Errore salvataggio stato');
        showToast(nextState ? 'Azienda completata' : 'Azienda rimessa da fare');
    } catch (e) {
        showToast(e.message, true);
    }
}

function copiaNominativoAzienda(id, ev) {
    if (ev) ev.stopPropagation();
    const a = aziende.find(x => Number(x.id) === Number(id));
    if (!a || !a.nominativo) return;
    navigator.clipboard.writeText(a.nominativo)
        .then(() => showToast(`Copiato: ${a.nominativo}`))
        .catch(() => showToast('Errore copia negli appunti', true));
}

function apriAziendaMessageModal(id) {
    const a = aziende.find(x => Number(x.id) === Number(id));
    if (!a) {
        showToast('Azienda non trovata', true);
        return;
    }
    const c = contattoDaAzienda(a);
    if (window.MessageTemplateModal) {
        window.MessageTemplateModal.open({
            contact: c,
            smsEndpoint: '/api/lettere/invia-singolo-sms',
            emailEndpoint: '/api/distinte/invia-singola-email',
            context: 'lettere_aziende',
            onSmsSent: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_pian_filone || contact.data_lettera || contact.data_intervento)) || c.data_pian_filone || c.data_lettera || c.data_intervento || '';
                const notaInvio = `mandato SMS per ${dataIntervento}`;
                await aggiungiNotaAzienda(a.id, notaInvio, { dataIntervento });
            },
            onEmailSent: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_pian_filone || contact.data_lettera || contact.data_intervento)) || c.data_pian_filone || c.data_lettera || c.data_intervento || '';
                await aggiungiNotaAzienda(a.id, 'mandata mail', { dataIntervento });
            },
            onWaOpened: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_pian_filone || contact.data_lettera || contact.data_intervento)) || c.data_pian_filone || c.data_lettera || c.data_intervento || '';
                await aggiungiNotaAzienda(a.id, 'mandato wa', { dataIntervento });
            },
            onMailOpened: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_pian_filone || contact.data_lettera || contact.data_intervento)) || c.data_pian_filone || c.data_lettera || c.data_intervento || '';
                await aggiungiNotaAzienda(a.id, 'mandata mail', { dataIntervento });
            }
        });
    } else {
        showToast('Modulo template non ancora caricato', true);
    }
}

function renderAziendaCard(a) {
    const checked = selectedAziendeIds.has(Number(a.id));
    const isCompleted = isAziendaCompleted(a);
    const emails = splitEmails(a.email);
    const missing = hasMissingContact(a);
    const note = (a.note || a.note_filone || '').trim();
    const tipoCe = (a.tipo_misuratore || '').trim();
    const tipoStr = tipoCe.toLowerCase();
    let tipoBadge = '';
    if (tipoStr.includes('semidirett')) {
        tipoBadge = '<span class="badge" style="background:#8b5cf6;font-size:0.65rem;color:white;">SEMIDIRETTA</span>';
    } else if (tipoStr.includes('tri')) {
        tipoBadge = '<span class="badge" style="background:#3b82f6;font-size:0.65rem;color:white;">TRIFASE</span>';
    } else if (tipoStr.includes('mono')) {
        tipoBadge = '<span class="badge" style="background:#10b981;font-size:0.65rem;color:white;">MONOFASE</span>';
    }

    const inviiLabel = a.invii_ok > 0
        ? `<span class="badge inviata" title="Inviata ${fmtTs(a.last_sent_ts)}"><i class="fas fa-check"></i> Inviata (${a.invii_ok})</span>`
        : '<span class="badge non-inviata">Non inviata</span>';

    const cardClass = [
        'contact-card',
        'azienda-card',
        isCompleted ? 'completed' : '',
        missing ? 'incomplete-row' : '',
        checked ? 'selected' : '',
        isCompleted ? 'row-risposta' : '',
        a.invii_ok > 0 ? 'row-inviata' : ''
    ].filter(Boolean).join(' ');

    const cambioData = checkCambioData(a.data_pianificazione, a.data_pian_filone);
    let dataInterventoHtml = '';
    if (!cambioData.cambiato) {
        dataInterventoHtml = `<div class="date"><i class="fas fa-calendar"></i> ${esc(a.data_pianificazione || '-')} ${a.ora_pianificazione ? '— ' + esc(a.ora_pianificazione) : ''}</div>`;
    } else if (cambioData.isAnnullato) {
        dataInterventoHtml = `
            <div class="date" style="display:flex;align-items:center;flex-wrap:gap;gap:6px;">
                <span class="badge badge-annullato" title="Intervento contrassegnato come ANNULLATO nel database locale"><i class="fas fa-ban"></i> ANNULLATO</span>
                <span style="font-size:0.75rem;color:var(--text-muted);">Lettera: <del>${esc(a.data_pianificazione)}</del></span>
            </div>
        `;
    } else {
        dataInterventoHtml = `
            <div class="date" style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;">
                <i class="fas fa-calendar-check" style="color:#f59e0b;"></i>
                <strong>${esc(cambioData.dataAttuale)}</strong>
                ${a.ora_pianificazione ? `<span style="font-size:0.78rem;">— ${esc(a.ora_pianificazione)}</span>` : ''}
                <span class="badge badge-cambio-data" title="Data modificata nel gestionale: da ${esc(a.data_pianificazione)} a ${esc(cambioData.dataAttuale)}">
                    <i class="fas fa-history"></i> Lettera: <del>${esc(a.data_pianificazione)}</del>
                </span>
            </div>
        `;
    }

    return `
        <div class="${cardClass}" data-id="${a.id}">
            <div class="contact-header" style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
                <label style="display:inline-flex;align-items:center;gap:6px;font-size:0.8rem;font-weight:700;color:var(--text-muted);cursor:pointer;user-select:none;">
                    <input type="checkbox" class="row-check" data-id="${a.id}" ${checked ? 'checked' : ''} onchange="onRowCheckChange(this)">
                    <span>Seleziona</span>
                </label>
                <div style="display:flex;align-items:center;gap:6px;">
                    ${tipoBadge}
                    ${inviiLabel}
                </div>
            </div>

            <div class="contact-name-row" style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;">
                <div style="flex:1;">
                    <div class="contact-name" style="font-size:0.98rem;font-weight:700;">
                        ${esc(a.nominativo || 'Senza nominativo')}
                        ${missing ? '<span class="badge da-completare" style="margin-left:6px;font-size:0.65rem;" title="Recapiti mancanti">Da completare</span>' : ''}
                    </div>
                    <div class="meta-sub" style="margin-top:2px;">
                        <span>${esc(a.comune || '')}</span>
                        ${a.via ? `· <span>${esc(a.via)} ${esc(a.civico || '')}</span>` : ''}
                    </div>
                </div>
                ${a.nominativo ? `
                    <button class="btn-copy-name" onclick="copiaNominativoAzienda(${a.id}, event)" title="Copia nominativo">
                        <i class="fas fa-copy"></i>
                    </button>
                ` : ''}
            </div>

            <div class="contact-details">
                <div><i class="fas fa-barcode"></i> POD: <b>${esc(a.pod || '')}</b></div>
                ${dataInterventoHtml}
                ${a.operatore ? `<div><i class="fas fa-user-cog"></i> Operatore: <b>${esc(a.operatore)}</b></div>` : ''}
                ${(a.codice_ante || a.matricola_ante) ? `<div><i class="fas fa-tachometer-alt"></i> Misuratore: ${esc([a.codice_ante, a.matricola_ante].filter(Boolean).join(' / '))}</div>` : ''}
                ${a.posizione_mis ? `<div><i class="fas fa-map-pin"></i> Posizione: ${esc(a.posizione_mis)}</div>` : ''}
            </div>

            <div style="display:flex;flex-direction:column;gap:8px;padding-top:8px;border-top:1px solid var(--border,#334155);">
                <div>
                    <label style="font-size:0.72rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;">Telefono</label>
                    <input class="tel-input edit-input ${a.telefono ? 'has-val' : ''}" style="width:100%;margin-top:2px;" value="${esc(a.telefono || '')}" placeholder="Telefono azienda..."
                           onkeydown="if(event.key==='Enter'){event.preventDefault(); saveInlineContact(${a.id}, 'telefono', this.value);}"
                           onblur="saveInlineContact(${a.id}, 'telefono', this.value)">
                </div>
                <div>
                    <label style="font-size:0.72rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;">Email / PEC</label>
                    <input class="email-input edit-input ${a.email ? 'has-val' : ''}" style="width:100%;margin-top:2px;" value="${esc(a.email || '')}"
                           placeholder="mail@dominio.it / pec@pec.it"
                           onkeydown="if(event.key==='Enter'){event.preventDefault(); saveInlineContact(${a.id}, 'email', this.value);}"
                           onblur="saveInlineContact(${a.id}, 'email', this.value)">
                    <div class="email-badges" style="margin-top:4px;">
                        ${emails.length ? emails.map(e => `<span class="badge ${e.pec ? 'pec' : ''}">${e.pec ? 'PEC' : 'MAIL'}</span>`).join('') : ((a.email || '').trim() ? `<span class="badge" style="background:var(--surface-raised, #334155);color:var(--text-muted);font-size:0.65rem;">${esc(a.email)}</span>` : '')}
                    </div>
                </div>
                <div>
                    <label style="font-size:0.72rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;display:flex;align-items:center;justify-content:space-between;">
                        <span>Stato e Note</span>
                        <label class="risposta-label" style="font-size:0.75rem;cursor:pointer;user-select:none;" title="Contrassegna come completata">
                            <input type="checkbox" class="risposta-check" ${isCompleted ? 'checked' : ''}
                                   onchange="toggleCompletedAzienda(${a.id})">
                            <span>Fatto</span>
                        </label>
                    </label>
                    <div style="display:flex;align-items:center;gap:4px;margin-top:2px;position:relative;">
                        <input class="note-input edit-input ${note ? 'has-note' : ''}" id="noteDisplay_azienda_${a.id}"
                               style="width:100%;" value="${esc(note)}" placeholder="Nota..."
                               onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaNotaAzienda(${a.id}, this.value);}"
                               onblur="salvaNotaAzienda(${a.id}, this.value)">
                        <div class="btn-add-note" onclick="toggleNoteDropdownAzienda(${a.id})" style="padding:4px 7px;font-size:0.75rem;" title="Aggiungi nota rapida">
                            <i class="fas fa-plus"></i>
                            <div class="note-dropdown" id="noteDropdown_card_azienda_${a.id}">
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'mandata mail')"><i class="fas fa-envelope" style="color: #6366f1"></i> Mandata mail</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'mandato wa')"><i class="fab fa-whatsapp" style="color: #25d366"></i> Mandato WA</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'mandato sms')"><i class="fas fa-sms" style="color: #10b981"></i> Mandato SMS</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'nr')"><i class="fas fa-phone-slash" style="color: #ef4444"></i> NR</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'chiamare')"><i class="fas fa-phone" style="color: #3b82f6"></i> Chiamare</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaAzienda(${a.id}, 'confermato')"><i class="fas fa-check-circle" style="color: #10b981"></i> Confermato</div>
                            </div>
                        </div>
                    </div>
                    ${(a.note_filone && a.note_filone !== a.note) ? `
                        <div class="meta-sub" style="font-size:0.75rem;color:var(--text-muted);margin-top:4px;">
                            <i class="fas fa-sticky-note"></i> Filone: ${esc(a.note_filone)}
                        </div>
                    ` : ''}
                </div>
            </div>

            <div class="contact-actions" style="margin-top:6px;display:flex;gap:8px;">
                <button type="button" class="btn-soft" onclick="apriAziendaMessageModal(${a.id})" style="flex:1;font-weight:700;padding:6px 10px;font-size:0.82rem;display:inline-flex;align-items:center;justify-content:center;gap:6px;border-radius:6px;color:#3b82f6;border-color:rgba(59,130,246,0.35);" title="Invia o componi con Template Messaggi">
                    <i class="fas fa-paper-plane"></i> Template
                </button>
                ${renderSearchShortcuts(a)}
            </div>
        </div>
    `;
}

function onRowCheckChange(cb) {
    const id = Number(cb.dataset.id);
    if (cb.checked) {
        selectedAziendeIds.add(id);
    } else {
        selectedAziendeIds.delete(id);
    }
    document.querySelectorAll(`.row-check[data-id="${id}"]`).forEach(input => {
        input.checked = cb.checked;
        const tr = input.closest('tr');
        if (tr) tr.classList.toggle('selected', cb.checked);
        const card = input.closest('.contact-card');
        if (card) card.classList.toggle('selected', cb.checked);
    });
    updateBulkButton();
}

async function saveInlineContact(id, field, value) {
    const row = aziende.find(a => Number(a.id) === Number(id));
    if (!row) return;
    if ((row[field] || '') === (value || '').trim()) return;
    try {
        const res = await fetch(`/api/lettere/aziende/${id}/contatto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [field]: value.trim() })
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Errore salvataggio contatto');
        row[field] = value.trim();
        renderAziende();
        showToast('Contatto salvato');
    } catch (e) {
        showToast(e.message, true);
    }
}

async function saveRisposta(id, checked, noteValue) {
    if (checked !== undefined) {
        const a = aziende.find(x => Number(x.id) === Number(id));
        if (a && isAziendaCompleted(a) !== Boolean(checked)) {
            await toggleCompletedAzienda(id);
        }
    }
    if (noteValue !== undefined) {
        await salvaNotaAzienda(id, noteValue);
    }
}

function selectedAziende() {
    return aziende.filter(a => selectedAziendeIds.has(Number(a.id)));
}

function toggleSelectAll(checked) {
    const visible = visibleAziende();
    visible.forEach(a => {
        if (checked) {
            selectedAziendeIds.add(Number(a.id));
        } else {
            selectedAziendeIds.delete(Number(a.id));
        }
    });
    document.querySelectorAll('.row-check').forEach(cb => {
        const id = Number(cb.dataset.id);
        cb.checked = selectedAziendeIds.has(id);
        const tr = cb.closest('tr');
        if (tr) tr.classList.toggle('selected', cb.checked);
        const card = cb.closest('.contact-card');
        if (card) card.classList.toggle('selected', cb.checked);
    });
    const selAll = document.getElementById('selectAll');
    if (selAll) selAll.checked = checked;
    updateBulkButton();
}

function updateBulkButton() {
    const count = selectedAziende().filter(a => splitEmails(a.email).length).length;
    const totalSelected = selectedAziende().length;
    const bulkBtn = document.getElementById('bulkOpenBtn');
    if (bulkBtn) {
        bulkBtn.disabled = count === 0;
        if (totalSelected > 0) {
            bulkBtn.innerHTML = `<i class="fas fa-paper-plane"></i> Invio bulk email (${totalSelected})`;
        } else {
            bulkBtn.innerHTML = `<i class="fas fa-paper-plane"></i> Invio bulk email`;
        }
    }
}

function getDefaultAllegati(tipo) {
    const prov = (generazioni[0]?.provincia || '').toUpperCase();
    const base = [...((allegatiInfo.default_provincia || {})[prov] || [])];
    if ((tipo || '').toLowerCase().includes('semidirett') && allegatiInfo.allegato_semidiretta) {
        base.push(allegatiInfo.allegato_semidiretta);
    }
    return base;
}

async function caricaStatoSmtpLettere() {
    try {
        const resp = await fetch('/api/smtp-settings');
        if (!resp.ok) return;
        const data = await resp.json();
        const configured = Boolean(data.host && (data.password_set || data.password));
        const textEl = document.getElementById('lettereSmtpStatusText');
        if (textEl) {
            if (configured) {
                textEl.innerHTML = `<span style="color:#10b981;font-weight:600;"><i class="fas fa-check-circle"></i> SMTP Configurato:</span> <code>${esc(data.host)}</code> (${esc(data.from_email || data.username || '')})`;
            } else {
                textEl.innerHTML = `<span style="color:#f59e0b;font-weight:600;"><i class="fas fa-exclamation-triangle"></i> SMTP non configurato:</span> clicca su Configura per inserire credenziali.`;
            }
        }
    } catch (e) {
        console.warn('Errore verifica stato SMTP lettere:', e);
    }
}
window.caricaStatoSmtpLettere = caricaStatoSmtpLettere;

function openBulkModal() {
    caricaStatoSmtpLettere();
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
                tipo_misuratore: a.tipo_misuratore || '',
                note: (a.note_appuntamento || a.note || a.note_filone || '').trim(),
                allegati: getDefaultAllegati(a.tipo_misuratore),
                azienda: a
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
    list.innerHTML = bulkItems.map((b, i) => {
        const tipoStr = (b.tipo_misuratore || '').toLowerCase();
        let tipoBadge = '';
        if (tipoStr.includes('semidirett')) {
            tipoBadge = `<span class="badge" style="background:#8b5cf6;font-size:0.68rem;">SEMIDIRETTA</span>`;
        } else if (tipoStr.includes('tri')) {
            tipoBadge = `<span class="badge" style="background:#3b82f6;font-size:0.68rem;">TRIFASE</span>`;
        } else if (tipoStr.includes('mono')) {
            tipoBadge = `<span class="badge" style="background:#10b981;font-size:0.68rem;">MONOFASE</span>`;
        } else if (b.tipo_misuratore) {
            tipoBadge = `<span class="badge" style="background:#64748b;font-size:0.68rem;">${esc(b.tipo_misuratore)}</span>`;
        }

        const noteHtml = b.note ? `
            <div class="bulk-item-note" style="margin-top:4px;font-size:0.8rem;color:#f59e0b;display:flex;align-items:center;gap:5px;font-weight:600;">
                <i class="fas fa-sticky-note"></i>
                <span title="${esc(b.note)}">${esc(b.note)}</span>
            </div>
        ` : '';

        return `
            <div class="bulk-item" style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--border);gap:12px;">
                <div style="display:flex;align-items:flex-start;gap:10px;flex:1;min-width:0;">
                    <input type="checkbox" class="bulk-check" data-idx="${i}" data-pec="${b.pec ? 'true' : 'false'}" data-tipo="${esc(b.tipo_misuratore)}" data-hasnote="${b.note ? 'true' : 'false'}" onchange="updateBulkSelectedCount()" checked style="margin-top:3px;">
                    <div style="flex:1;min-width:0;">
                        <div class="bulk-name" style="font-weight:700;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                            ${esc(b.nome)}
                            ${b.pec ? '<span class="badge pec">PEC</span>' : '<span class="badge">MAIL</span>'}
                            ${tipoBadge}
                        </div>
                        <div class="mini" style="margin-top:2px;">${esc(b.email)} · ${esc(b.pod || '')}</div>
                        ${noteHtml}
                    </div>
                </div>
                <div class="mini" style="white-space:nowrap;">${b.allegati.length ? b.allegati.length + ' allegati' : 'no allegati'}</div>
            </div>
        `;
    }).join('');
    updateBulkSelectedCount();
}

function applyBulkPreset(preset) {
    document.querySelectorAll('.bulk-filter-presets .btn-preset').forEach(b => b.classList.remove('active'));
    const activeBtn = document.getElementById(`presetBtn_${preset}`);
    if (activeBtn) activeBtn.classList.add('active');

    const checkboxes = document.querySelectorAll('.bulk-check');
    checkboxes.forEach(cb => {
        const isPec = cb.dataset.pec === 'true';
        const tipo = (cb.dataset.tipo || '').toLowerCase();
        const hasNote = cb.dataset.hasnote === 'true';

        if (preset === 'tutti') {
            cb.checked = true;
        } else if (preset === 'solo_mail') {
            cb.checked = !isPec;
        } else if (preset === 'solo_pec') {
            cb.checked = isPec;
        } else if (preset === 'monofase') {
            cb.checked = tipo.includes('mono');
        } else if (preset === 'trifase') {
            cb.checked = tipo.includes('tri') && !tipo.includes('semidirett');
        } else if (preset === 'semidiretta') {
            cb.checked = tipo.includes('semidirett');
        } else if (preset === 'con_note') {
            cb.checked = hasNote;
        } else if (preset === 'senza_note') {
            cb.checked = !hasNote;
        }
    });
    updateBulkSelectedCount();
}

function updateBulkSelectedCount() {
    const total = document.querySelectorAll('.bulk-check').length;
    const checked = document.querySelectorAll('.bulk-check:checked').length;
    const countEl = document.getElementById('bulkSelectedCount');
    if (countEl) countEl.textContent = `${checked} / ${total}`;
    const allCb = document.getElementById('bulkSelectAll');
    if (allCb) allCb.checked = checked === total && total > 0;
}

function toggleBulkSelectAll(checked) {
    document.querySelectorAll('.bulk-check').forEach(cb => {
        cb.checked = checked;
    });
    document.querySelectorAll('.bulk-filter-presets .btn-preset').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(checked ? 'presetBtn_tutti' : '');
    if (btn) btn.classList.add('active');
    updateBulkSelectedCount();
}

function contattoDaAzienda(a, emailDest) {
    const matricola = [a.codice_ante, a.matricola_ante].filter(Boolean).join(' / ');
    return {
        id: a.id,
        pod: a.pod || '',
        email: emailDest || a.email || '',
        telefono: a.telefono || '',
        telefono_filone: a.telefono || '',
        nome: a.nominativo || '',
        indirizzo: a.via || '',
        civico: a.civico || '',
        comune: a.comune || '',
        data_intervento: a.data_pianificazione || '',
        data_lettera: a.data_pianificazione || '',
        data_pian_filone: a.data_pian_filone || '',
        pod_filone: a.pod || '',
        eneltel_filone: a.eneltel || '',
        matricola_filone: matricola,
        tipologia_contatore: a.tipo_misuratore || '',
        nome_societa: a.nominativo || '',
        nome_referente: '',
        note: (a.note_appuntamento || a.note || a.note_filone || '').trim(),
    };
}

function buildMessageHtml(bodyText) {
    const prov = (window.provincia || generazioni[0]?.provincia || (window.BulkEmailPreview && BulkEmailPreview.getDetectedProvince()) || 'TORINO').toUpperCase();
    const provSettings = (templateSettings.province && templateSettings.province[prov]) || {};
    const provKey = 'email_' + prov.toLowerCase();
    const emailTecnico = provSettings.email || templateSettings[provKey] || templateSettings.email_tecnico || `tecnico_${prov.toLowerCase()}@algecopower.it`;
    const telefonoTecnico = provSettings.telefono || templateSettings['telefono_' + prov.toLowerCase()] || templateSettings.telefono_tecnico || '011 025 2702';
    return BulkEmailPreview.buildMessageHtml(bodyText, templateSettings.firma_html || '', emailTecnico, telefonoTecnico, prov);
}

function generaAnteprima() {
    const templateId = document.getElementById('templateSelect').value;
    if (!templateId) {
        showToast('Seleziona un template', true);
        return;
    }
    const tmpl = TEMPLATES[templateId];
    if (!tmpl) {
        showToast('Template non valido', true);
        return;
    }
    const checkboxes = document.querySelectorAll('.bulk-check:checked');
    if (checkboxes.length === 0) {
        showToast('Nessun contatto selezionato', true);
        return;
    }
    const prov = (window.provincia || generazioni[0]?.provincia || (window.BulkEmailPreview && BulkEmailPreview.getDetectedProvince()) || 'TORINO').toUpperCase();
    const provSettings = (templateSettings.province && templateSettings.province[prov]) || {};
    const provKey = 'email_' + prov.toLowerCase();
    const emailTecnico = provSettings.email || templateSettings[provKey] || templateSettings.email_tecnico || `tecnico_${prov.toLowerCase()}@algecopower.it`;
    const telefonoTecnico = provSettings.telefono || templateSettings['telefono_' + prov.toLowerCase()] || templateSettings.telefono_tecnico || '011 025 2702';

    // Raccogli gli elementi selezionati
    const selectedRaw = [];
    checkboxes.forEach(cb => {
        const idx = Number(cb.dataset.idx);
        const b = bulkItems[idx];
        if (!b) return;
        const a = aziende.find(item => Number(item.id) === Number(b.azienda_id)) || {};
        selectedRaw.push({
            ...b,
            azienda: a,
            contatto: contattoDaAzienda(a, b.email),
        });
    });

    // Raggruppa per azienda/destinatario
    const grouped = BulkEmailPreview.groupContattiPerAzienda(selectedRaw, item => {
        const nom = (item.nome || item.azienda.nominativo || '').trim().toUpperCase();
        const email = (item.email || '').trim().toLowerCase();
        const com = (item.azienda.comune || '').trim().toUpperCase();
        return `${nom}__${com}__${email}`;
    });

    bulkPreviews = [];
    grouped.forEach(group => {
        const first = group[0];
        const contatoriList = group.map(g => g.contatto);
        const aziendaIds = group.map(g => g.azienda_id);

        const content = BulkEmailPreview.buildPluralContent(tmpl, contatoriList, FIELD_DEFS, emailTecnico, telefonoTecnico);

        const allegatiSet = new Set(first.allegati || []);
        if (content.excelFilename) {
            allegatiSet.add(content.excelFilename);
        }

        bulkPreviews.push({
            azienda_id: first.azienda_id,
            azienda_ids: aziendaIds,
            contatori: contatoriList,
            pod: contatoriList.length === 1 ? (contatoriList[0].pod || '') : `${contatoriList.length} POD`,
            email: first.email,
            pec: first.pec,
            nome: first.nome || first.azienda.nominativo || 'Azienda',
            subject: content.subject,
            bodyHtml: content.bodyText,
            contatto: first.contatto,
            allegati: Array.from(allegatiSet),
            edited: false,
        });
    });

    if (bulkPreviews.length === 0) {
        showToast('Nessuna anteprima generata', true);
        return;
    }

    window.bulkPreviews = bulkPreviews;
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
    window.bulkPreviews = bulkPreviews;
    const container = document.getElementById('bulkPreviewCards');
    const tuttiAllegati = allegatiInfo.allegati_disponibili || [];
    container.innerHTML = bulkPreviews.map((p, i) => {
        const renderedHtml = buildMessageHtml(p.bodyHtml);
        const allegatiHtml = BulkEmailPreview.renderAllegatiList(p.allegati || [], i, tuttiAllegati, 'rimuoviAllegato', 'aggiungiAllegato');
        const numContatori = (p.contatori && p.contatori.length) || 1;
        const contatoriBadge = numContatori > 1 ? `<span class="badge" style="background:#6366f1;font-weight:700;white-space:nowrap;flex-shrink:0;">${numContatori} contatori</span>` : '';
        const podText = numContatori === 1
            ? esc(p.pod || '')
            : (numContatori === 2 ? `${p.contatori[0]?.pod || ''}, ${p.contatori[1]?.pod || ''}` : '');

        // Nota di intestazione
        let headerNoteHtml = '';
        if (numContatori === 1 && p.contatori[0]?.note) {
            headerNoteHtml = `<span class="preview-note" title="${esc(p.contatori[0].note)}"><i class="fas fa-sticky-note"></i> ${esc(p.contatori[0].note)}</span>`;
        } else if (numContatori > 1) {
            const withNotes = p.contatori.filter(c => c.note);
            if (withNotes.length > 0) {
                const noteSummary = withNotes.map(c => `${c.pod}: ${c.note}`).join(' | ');
                headerNoteHtml = `<span class="preview-note" title="${esc(noteSummary)}"><i class="fas fa-sticky-note"></i> ${withNotes.length} con note</span>`;
            }
        }

        // Sezione dettagli contatori e note
        let contatoriDetailHtml = '';
        if (numContatori === 1) {
            const singleNote = p.contatori[0]?.note;
            if (singleNote) {
                contatoriDetailHtml = `
                    <div class="preview-note-box">
                        <i class="fas fa-sticky-note"></i>
                        <span><strong>Nota contatore:</strong> ${esc(singleNote)}</span>
                    </div>
                `;
            }
        } else {
            contatoriDetailHtml = `
                <div class="preview-contatori-section">
                    <div class="preview-field-label" style="margin-top:0;"><i class="fas fa-list-check"></i> Elenco contatori e note (${numContatori}):</div>
                    <div class="preview-contatori-list">
                        ${p.contatori.map((c, idx) => `
                            <div class="preview-contatore-item">
                                <div class="preview-contatore-main">
                                    <span class="preview-contatore-idx">#${idx + 1}</span>
                                    <span class="preview-contatore-pod"><strong>POD:</strong> ${esc(c.pod || '')}</span>
                                    ${c.indirizzo ? `<span class="preview-contatore-addr">${esc(c.indirizzo)} ${esc(c.civico || '')} (${esc(c.comune || '')})</span>` : ''}
                                    ${c.tipologia_contatore ? `<span class="badge" style="font-size:0.7rem;">${esc(c.tipologia_contatore)}</span>` : ''}
                                </div>
                                ${c.note ? `
                                    <div class="preview-contatore-note-box">
                                        <i class="fas fa-sticky-note"></i>
                                        <span><strong>Nota:</strong> ${esc(c.note)}</span>
                                    </div>
                                ` : ''}
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        return `
            <div class="preview-card ${i === 0 ? 'expanded' : ''}" id="previewCard${i}">
                <div class="preview-card-header" onclick="togglePreviewCard(${i})">
                    <i class="fas fa-chevron-right preview-toggle"></i>
                    <span class="preview-dest">
                        <span class="preview-dest-text">${esc(p.nome)} — ${esc(p.email)}</span>
                        ${contatoriBadge}
                        ${p.pec ? '<span class="badge pec">PEC</span>' : '<span class="badge">MAIL</span>'}
                    </span>
                    ${podText ? `<span class="preview-pod">${podText}</span>` : ''}
                    ${headerNoteHtml}
                    ${(p.allegati || []).length > 0 ? `<span class="preview-allegati-badge"><i class="fas fa-paperclip"></i> ${p.allegati.length}</span>` : ''}
                    <span class="preview-card-badge edited" id="previewBadge${i}" style="${p.edited ? '' : 'display:none;'}">modificata</span>
                </div>
                <div class="preview-card-body">
                    ${contatoriDetailHtml}
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
    if (bulkPreviews.length === 0) {
        showToast('Nessuna email pronta', true);
        return;
    }
    if (!confirm(`Confermi l'invio di ${bulkPreviews.length} comunicazioni email/PEC?`)) return;

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

    const runner = BulkEmailPreview.createBulkRunner({
        items: bulkPreviews,
        pauseBetweenSec: PAUSA_TRA_EMAIL_SEC || 3,
        sendItemFn: async (item, i) => {
            const subjInput = document.getElementById(`previewSubject${i}`);
            const currentSubject = subjInput ? subjInput.value : item.subject;
            const editorEl = document.getElementById(`previewBody${i}`);
            const currentBody = (editorEl && document.getElementById(`previewBodyContainer${i}`)?.style.display === 'block')
                ? editorEl.innerHTML
                : item.bodyHtml;

            const res = await fetch('/api/lettere/invia-email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    azienda_id: item.azienda_id,
                    azienda_ids: item.azienda_ids || [item.azienda_id],
                    contatori: item.contatori,
                    email: item.email,
                    template_id: templateId,
                    subject_override: currentSubject,
                    body_override: currentBody,
                    allegati: item.allegati,
                })
            });
            return await res.json();
        },
        onProgress: ({ index, total, percent }) => {
            text.textContent = `${index + 1} / ${total}`;
            fill.style.width = `${percent}%`;
        },
        onItemSuccess: (item, i, data) => {
            const countText = item.contatori && item.contatori.length > 1 ? ` (${item.contatori.length} contatori)` : '';
            log.innerHTML += `<div class="log-ok">OK ${esc(item.email)}${countText}</div>`;
            log.scrollTop = log.scrollHeight;
        },
        onItemError: (item, i, data) => {
            log.innerHTML += `<div class="log-err">Errore ${esc(item.email)}: ${esc(data.errore || data.error || 'Errore')}</div>`;
            log.scrollTop = log.scrollHeight;
        },
        onPauseCountdown: ({ timeLeft, index, total }) => {
            text.textContent = `${index + 1} / ${total} — attesa ${timeLeft}s...`;
        },
        onComplete: async ({ nSent, nErrors, total, aborted }) => {
            sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Invia selezionate';
            sendBtn.onclick = sendBulk;
            text.textContent = `${nSent} inviate, ${nErrors} errori`;
            if (aborted) {
                log.innerHTML += `<div class="log-err">Invio interrotto dall'utente dopo ${nSent} invii</div>`;
            }
            showToast(`${nSent} invii completati${nErrors ? ', ' + nErrors + ' errori' : ''}`, Boolean(nErrors));
            await loadAziende();
            await loadStats();
        }
    });

    sendBtn.onclick = () => { runner.abort(); };
    await runner.start();
}

/* ==========================================================================
   GESTIONE UTENTI PRIVATI (Viste, Card, Filtri, Appuntamenti, Bulk SMS)
   ========================================================================== */

function switchMainView(view) {
    currentMainView = view;
    const tabAz = document.getElementById('tabViewAziende');
    const tabPr = document.getElementById('tabViewPrivati');
    const secAz = document.getElementById('sectionAziende');
    const secPr = document.getElementById('sectionPrivati');

    if (tabAz) tabAz.classList.toggle('active', view === 'aziende');
    if (tabPr) tabPr.classList.toggle('active', view === 'privati');
    if (secAz) secAz.style.display = view === 'aziende' ? 'block' : 'none';
    if (secPr) secPr.style.display = view === 'privati' ? 'block' : 'none';

    if (view === 'privati') {
        if (!privati.length) {
            loadPrivati();
        } else {
            renderPrivati();
        }
    } else {
        renderAziende();
    }
}

async function loadPrivati() {
    if (privatiController) privatiController.abort();
    privatiController = new AbortController();
    const grid = document.getElementById('privatiGrid');
    if (grid && (!privati || !privati.length)) {
        grid.innerHTML = '<div class="empty-state"><i class="fas fa-spinner fa-spin"></i><h3>Caricamento utenti privati...</h3></div>';
    }
    const params = new URLSearchParams();
    if (selectedGenerazione) params.set('generazione_id', selectedGenerazione);
    params.set('limit', '5000');

    try {
        const res = await fetch(`/api/lettere/privati?${params.toString()}`, {
            signal: privatiController.signal
        });
        const data = await res.json();
        if (!data.success) {
            showToast(data.error || 'Errore caricamento privati', true);
            return;
        }
        privati = data.contatti || [];
        const badgePr = document.getElementById('badgeCountPrivati');
        if (badgePr) badgePr.textContent = data.totale || privati.length;

        // Inizializza noteStates dai contatti
        for (const c of privati) {
            if (c.pod) {
                noteStatesPrivati[c.pod] = c.note_filone || c.note || c.note_appuntamento || '';
            }
        }

        renderPrivati();
    } catch (e) {
        if (e.name !== 'AbortError') {
            showToast(e.message || 'Errore caricamento privati', true);
        }
    }
}

let privatiSearchDebounce = null;
function onPrivatiSearchChange() {
    clearTimeout(privatiSearchDebounce);
    privatiSearchDebounce = setTimeout(renderPrivati, 200);
}

let privatiViewMode = localStorage.getItem('lettere_privati_view_mode') || 'table';

function setPrivatiViewMode(mode) {
    privatiViewMode = mode;
    localStorage.setItem('lettere_privati_view_mode', mode);
    document.getElementById('btnPrivatiViewTable')?.classList.toggle('active', mode === 'table');
    document.getElementById('btnPrivatiViewCards')?.classList.toggle('active', mode === 'cards');
    renderPrivati();
}

function toggleFilterSoloModificati() {
    const cb = document.getElementById('filterPrivatiSoloModificati');
    if (cb) {
        cb.checked = !cb.checked;
        renderPrivati();
    }
}

let selectedPrivatiPods = new Set();

function getFilteredPrivati() {
    const showCell = document.getElementById('filterPrivatiCellulare')?.checked ?? true;
    const showFisso = document.getElementById('filterPrivatiFisso')?.checked ?? true;
    const showNoPhone = document.getElementById('filterPrivatiNoPhone')?.checked ?? true;
    const showCompleted = document.getElementById('filterPrivatiCompleted')?.checked ?? true;
    const showNotCompleted = document.getElementById('filterPrivatiNotCompleted')?.checked ?? true;
    const showSoloModificati = document.getElementById('filterPrivatiSoloModificati')?.checked || false;
    const q = (document.getElementById('searchPrivatiInput')?.value || '').toLowerCase().trim();

    return (privati || []).filter(c => {
        if (c.tipo === 'cellulare' && !showCell) return false;
        if (c.tipo === 'fisso' && !showFisso) return false;
        if (c.tipo === 'senza_telefono' && !showNoPhone) return false;

        const isCompleted = completedPrivati[c.pod] || false;
        if (isCompleted && !showCompleted) return false;
        if (!isCompleted && !showNotCompleted) return false;

        if (showSoloModificati && !checkCambioData(c.data_lettera || c.data_intervento, c.data_pian_filone).cambiato) {
            return false;
        }

        if (q) {
            const searchable = `${c.nome || ''} ${c.pod || ''} ${c.comune || ''} ${c.indirizzo || ''} ${c.telefono || ''} ${c.telefono_filone || ''}`.toLowerCase();
            if (!searchable.includes(q)) return false;
        }
        return true;
    });
}

function updatePrivatiBulkSmsButton() {
    const btn = document.getElementById('privatiBulkSmsBtn');
    if (!btn) return;
    const numSelected = selectedPrivatiPods.size;
    if (numSelected > 0) {
        const selCell = (privati || []).filter(c => selectedPrivatiPods.has(c.pod) && Boolean(estraiMigliorCellularePrivato(c)));
        btn.disabled = selCell.length === 0;
        btn.innerHTML = `<i class="fas fa-sms"></i> Invio Multiplo SMS (${numSelected} sel · ${selCell.length} cell)`;
        btn.title = `Invia SMS a ${selCell.length} contatti selezionati con cellulare`;
    } else {
        const cellDisponibili = (privati || []).filter(c => {
            if (c.tipo === 'cellulare' && (c.telefono || '').trim()) return true;
            if (c.numeri_alternativi && c.numeri_alternativi.some(n => n.tipo === 'cellulare' && (n.numero || '').trim())) return true;
            return false;
        });
        btn.disabled = cellDisponibili.length === 0;
        btn.innerHTML = `<i class="fas fa-sms"></i> Invio Multiplo SMS`;
        btn.title = cellDisponibili.length > 0
            ? `${cellDisponibili.length} utenti privati con cellulare per invio SMS`
            : 'Nessun utente privato con cellulare';
    }
}

function toggleSelectAllPrivati(checked) {
    const visible = getFilteredPrivati();
    if (checked) {
        visible.forEach(c => selectedPrivatiPods.add(c.pod));
    } else {
        visible.forEach(c => selectedPrivatiPods.delete(c.pod));
    }
    document.querySelectorAll('.privati-row-check').forEach(cb => {
        const pod = cb.getAttribute('data-pod');
        if (pod) cb.checked = selectedPrivatiPods.has(pod);
    });
    document.querySelectorAll('tr[data-pod]').forEach(tr => {
        const pod = tr.getAttribute('data-pod');
        tr.classList.toggle('selected', selectedPrivatiPods.has(pod));
    });
    document.querySelectorAll('.contact-card[data-pod]').forEach(card => {
        const pod = card.getAttribute('data-pod');
        card.classList.toggle('selected', selectedPrivatiPods.has(pod));
    });
    updatePrivatiBulkSmsButton();
}

function onPrivatiRowCheckChange(el) {
    const pod = el.getAttribute('data-pod');
    if (!pod) return;
    if (el.checked) {
        selectedPrivatiPods.add(pod);
    } else {
        selectedPrivatiPods.delete(pod);
    }
    document.querySelectorAll(`.privati-row-check[data-pod="${CSS.escape(pod)}"]`).forEach(cb => {
        cb.checked = el.checked;
    });
    document.querySelectorAll(`tr[data-pod="${CSS.escape(pod)}"]`).forEach(tr => {
        tr.classList.toggle('selected', el.checked);
    });
    document.querySelectorAll(`.contact-card[data-pod="${CSS.escape(pod)}"]`).forEach(card => {
        card.classList.toggle('selected', el.checked);
    });
    const selectAll = document.getElementById('selectAllPrivati');
    if (selectAll) {
        const visible = getFilteredPrivati();
        selectAll.checked = visible.length > 0 && visible.every(c => selectedPrivatiPods.has(c.pod));
    }
    updatePrivatiBulkSmsButton();
}

function renderPrivati() {
    const grid = document.getElementById('privatiGrid');
    const tableWrap = document.getElementById('privatiTableWrap');
    if (!grid && !tableWrap) return;

    document.getElementById('btnPrivatiViewTable')?.classList.toggle('active', privatiViewMode === 'table');
    document.getElementById('btnPrivatiViewCards')?.classList.toggle('active', privatiViewMode === 'cards');

    const filtered = getFilteredPrivati();

    // Conteggi
    const countCell = (privati || []).filter(c => c.tipo === 'cellulare').length;
    const countFisso = (privati || []).filter(c => c.tipo === 'fisso').length;
    const countNoPhone = (privati || []).filter(c => c.tipo === 'senza_telefono').length;
    const countComp = (privati || []).filter(c => completedPrivati[c.pod]).length;
    const countRipianificati = (privati || []).filter(c => checkCambioData(c.data_lettera || c.data_intervento, c.data_pian_filone).cambiato).length;

    const elCell = document.getElementById('privatiCountCellulare');
    const elFisso = document.getElementById('privatiCountFisso');
    const elNoPhone = document.getElementById('privatiCountNoPhone');
    const elComp = document.getElementById('privatiCountCompleted');
    const elRipianificati = document.getElementById('privatiCountRipianificati');
    const elRipianificatiBox = document.getElementById('privatiStatRipianificatiBox');

    if (elCell) elCell.textContent = countCell;
    if (elFisso) elFisso.textContent = countFisso;
    if (elNoPhone) elNoPhone.textContent = countNoPhone;
    if (elComp) elComp.textContent = `${countComp} / ${(privati || []).length}`;
    if (elRipianificati) elRipianificati.textContent = countRipianificati;
    if (elRipianificatiBox) {
        elRipianificatiBox.style.display = countRipianificati > 0 ? 'inline-flex' : 'none';
    }

    updatePrivatiBulkSmsButton();

    const { groups, sortedKeys } = groupContactsByDate(
        filtered,
        c => c.data_lettera || c.data_intervento,
        c => c.nome
    );

    if (privatiViewMode === 'table') {
        if (grid) grid.style.display = 'none';
        if (tableWrap) tableWrap.style.display = 'block';

        const tbody = document.getElementById('privatiTableBody');
        if (!tbody) return;

        if (!filtered.length) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="empty" style="padding: 30px; text-align: center; color: var(--text-muted);">
                        <i class="fas fa-filter" style="font-size: 1.5rem; margin-bottom: 8px; display: block;"></i>
                        Nessun utente privato trovato con i filtri correnti
                    </td>
                </tr>
            `;
            return;
        }

        let html = '';
        for (const dk of sortedKeys) {
            const items = groups.get(dk);
            const collapsed = collapsedDates.has(dk);
            const daFareInGroup = items.filter(c => !completedPrivati[c.pod]).length;
            const label = formatDateGroupHeader(dk);
            html += `
                <tr class="date-group-header-row" onclick="toggleDateGroup('${esc(dk)}')">
                    <td colspan="6">
                        <div class="date-group-header">
                            <i class="fas fa-chevron-${collapsed ? 'right' : 'down'}"></i>
                            <span class="date-group-label">${esc(label)}</span>
                            <span class="date-group-count">${items.length} utenti${daFareInGroup ? ` · ${daFareInGroup} da fare` : ''}</span>
                        </div>
                    </td>
                </tr>
            `;
            if (!collapsed) {
                html += items.map(c => renderPrivatoRow(c)).join('');
            }
        }
        tbody.innerHTML = html;

        const selectAll = document.getElementById('selectAllPrivati');
        if (selectAll) {
            selectAll.checked = filtered.length > 0 && filtered.every(c => selectedPrivatiPods.has(c.pod));
        }
    } else {
        if (tableWrap) tableWrap.style.display = 'none';
        if (grid) {
            grid.style.display = 'block';

            if (!filtered.length) {
                grid.innerHTML = `
                    <div class="empty-state" style="padding:40px 20px;text-align:center;color:var(--text-muted);">
                        <i class="fas fa-filter" style="font-size:2rem;margin-bottom:10px;display:block;"></i>
                        <h3>Nessun utente privato trovato</h3>
                        <p>Prova a modificare i filtri o il termine di ricerca</p>
                    </div>
                `;
                return;
            }

            let cardsHtml = '';
            for (const dk of sortedKeys) {
                const items = groups.get(dk);
                const collapsed = collapsedDates.has(dk);
                const daFareInGroup = items.filter(c => !completedPrivati[c.pod]).length;
                const label = formatDateGroupHeader(dk);
                cardsHtml += `
                    <div class="date-group-card-block">
                        <div class="date-group-header-card" onclick="toggleDateGroup('${esc(dk)}')">
                            <i class="fas fa-chevron-${collapsed ? 'right' : 'down'}"></i>
                            <span class="date-group-label">${esc(label)}</span>
                            <span class="date-group-count">${items.length} utenti${daFareInGroup ? ` · ${daFareInGroup} da fare` : ''}</span>
                        </div>
                        ${!collapsed ? `
                            <div class="contacts-grid">
                                ${items.map(c => renderPrivatoCard(c)).join('')}
                            </div>
                        ` : ''}
                    </div>
                `;
            }
            grid.innerHTML = cardsHtml;
        }
    }
}

function renderPrivatoRow(c) {
    const isCompleted = completedPrivati[c.pod] || false;
    const isSelected = selectedPrivatiPods.has(c.pod);
    const note = noteStatesPrivati[c.pod] || c.note_filone || '';
    const nomeContatto = c.nome || 'Nome non disponibile';
    const tipoCe = (c.tipologia_contatore || c.tipo_misuratore || '').trim();
    const tipoStr = tipoCe.toLowerCase();
    let tipoBadge = '';
    if (tipoStr.includes('semidirett')) {
        tipoBadge = '<span class="badge" style="background:#8b5cf6;font-size:0.65rem;color:white;">SEMIDIRETTA</span>';
    } else if (tipoStr.includes('tri')) {
        tipoBadge = '<span class="badge" style="background:#3b82f6;font-size:0.65rem;color:white;">TRIFASE</span>';
    } else if (tipoStr.includes('mono')) {
        tipoBadge = '<span class="badge" style="background:#10b981;font-size:0.65rem;color:white;">MONOFASE</span>';
    }

    const rowClass = [
        isCompleted ? 'completed' : '',
        isSelected ? 'selected' : '',
        checkCambioData(c.data_lettera || c.data_intervento, c.data_pian_filone).cambiato ? 'row-cambio-data' : ''
    ].filter(Boolean).join(' ');

    return `
        <tr class="${rowClass}" data-pod="${esc(c.pod)}">
            <td class="checkbox-cell" style="text-align:center;">
                <input type="checkbox" class="privati-row-check" data-pod="${esc(c.pod)}" ${isSelected ? 'checked' : ''} onchange="onPrivatiRowCheckChange(this)">
            </td>
            <td class="azienda-cell">
                <div class="azienda-name">
                    ${c.lavoro ? `<span class="lavoro-num" style="font-size:0.75rem;font-weight:700;color:var(--text-muted);margin-right:4px;">#${esc(c.lavoro)}</span>` : ''}
                    <span style="font-weight:700;">${esc(nomeContatto)}</span>
                    ${c.nome ? `
                        <button type="button" class="btn-mini" onclick="copiaNominativoPrivato('${esc(c.pod)}', event)" title="Copia nominativo" style="background:transparent;border:none;color:var(--text-muted);cursor:pointer;padding:0 3px;">
                            <i class="fas fa-copy"></i>
                        </button>
                    ` : ''}
                    <span class="type-badge ${c.tipo}" style="font-size:0.68rem;padding:2px 6px;">
                        ${c.tipo === 'cellulare' ? '<i class="fas fa-mobile-alt"></i> Cell' :
                          c.tipo === 'fisso' ? '<i class="fas fa-phone"></i> Fisso' :
                          '<i class="fas fa-phone-slash"></i> No Tel'}
                    </span>
                    ${tipoBadge}
                </div>
                <div class="azienda-sub">
                    <span><b>${esc(c.pod || '')}</b></span> ·
                    <span>${esc(c.comune || '')}</span>
                    ${c.indirizzo ? `· <span>${esc(c.indirizzo)} ${esc(c.civico || '')}</span>` : ''}
                    ${c.posizione_contatore ? `· <span title="Posizione contatore" style="color:var(--text-muted);"><i class="fas fa-map-pin" style="font-size:0.68rem;"></i> ${esc(c.posizione_contatore)}</span>` : ''}
                </div>
            </td>
            <td class="intervento-cell">
                ${renderDataInterventoHtml(c.data_lettera || c.data_intervento, c.data_pian_filone, c.ora_intervento)}
            </td>
            <td class="tel-cell" data-label="Telefono">
                <input type="text" class="tel-input edit-input ${c.telefono_filone ? 'has-val' : ''}" id="phoneTable_privato_${esc(c.pod)}"
                    value="${esc(c.telefono_filone || '')}" placeholder="Tel filone..."
                    onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaTelefonoPrivato('${esc(c.pod)}', this.value);}"
                    onblur="salvaTelefonoPrivato('${esc(c.pod)}', this.value)">
                ${(c.telefono && c.telefono.trim() && c.telefono.trim() !== (c.telefono_filone || '').trim()) ? `
                    <div style="margin-top:3px;">
                        <button type="button" class="btn-mini" onclick="aggiungiTelFilonePrivato('${esc(c.pod)}', '${esc(c.telefono)}')" style="font-size:0.7rem;padding:2px 6px;background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);border-radius:4px;cursor:pointer;" title="Aggiungi numero lettera al filone">
                            + ${esc(c.telefono)}
                        </button>
                    </div>
                ` : ''}
            </td>
            <td class="email-cell" data-label="Email">
                <input type="text" class="email-input edit-input ${c.email_filone ? 'has-val' : ''}" id="emailTable_privato_${esc(c.pod)}"
                    value="${esc(c.email_filone || '')}" placeholder="Email filone..."
                    onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaEmailPrivato('${esc(c.pod)}', this.value);}"
                    onblur="salvaEmailPrivato('${esc(c.pod)}', this.value)">
                ${c.email_filone ? '<div class="email-badges"><span class="badge">MAIL</span></div>' : ''}
            </td>
            <td class="invii-cell">
                <div class="risposta-row">
                    <label class="risposta-label">
                        <input type="checkbox" ${isCompleted ? 'checked' : ''} onchange="toggleCompletedPrivato('${esc(c.pod)}')">
                        <span>Fatto</span>
                    </label>
                    <div style="display:flex;align-items:center;gap:4px;flex:1;position:relative;">
                        <input type="text" class="note-input ${note ? 'has-note' : ''}" id="noteTable_privato_${esc(c.pod)}"
                            value="${esc(note)}" placeholder="Nota..."
                            onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaNotaPrivato('${esc(c.pod)}', this.value);}"
                            onblur="salvaNotaPrivato('${esc(c.pod)}', this.value)">
                        <div class="btn-add-note" onclick="toggleNoteDropdownPrivato('${esc(c.pod)}')" style="padding:4px 7px;font-size:0.75rem;" title="Aggiungi nota rapida">
                            <i class="fas fa-plus"></i>
                            <div class="note-dropdown" id="noteDropdown_privato_${esc(c.pod)}">
                                <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'mandato sms')"><i class="fas fa-sms" style="color: #10b981"></i> Mandato SMS</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'mandato wa')"><i class="fab fa-whatsapp" style="color: #25d366"></i> Mandato WA</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'nr')"><i class="fas fa-phone-slash" style="color: #ef4444"></i> NR</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'chiamare')"><i class="fas fa-phone" style="color: #3b82f6"></i> Chiamare</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'numero errato')"><i class="fas fa-times-circle" style="color: #ef4444"></i> Num errato</div>
                                <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'mandata mail')"><i class="fas fa-envelope" style="color: #6366f1"></i> Mandata mail</div>
                            </div>
                        </div>
                    </div>
                </div>
                <div style="margin-top:5px;">
                    <button type="button" class="btn-mini ${c.tipo === 'cellulare' ? 'btn-wa' : ''}" onclick="apriPrivatiMessageModal('${esc(c.pod)}')" title="Apri Template Messaggio" style="padding:3px 8px;font-size:0.75rem;border-radius:4px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;${c.tipo === 'cellulare' ? 'background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.3);' : 'background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);'}">
                        <i class="${c.tipo === 'cellulare' ? 'fab fa-whatsapp' : 'fas fa-paper-plane'}"></i> Template
                    </button>
                </div>
            </td>
        </tr>
    `;
}

function renderPrivatoCard(c) {
    const isCompleted = completedPrivati[c.pod] || false;
    const isSelected = selectedPrivatiPods.has(c.pod);
    const note = noteStatesPrivati[c.pod] || c.note_filone || '';
    const nomeContatto = c.nome || 'Nome non disponibile';
    const isErrato = erratiPrivati[c.pod] || false;
    const telDisplay = c.telefono || c.telefono_filone || '';

    return `
    <div class="contact-card ${c.tipo} ${isCompleted ? 'completed' : ''} ${isSelected ? 'selected' : ''}" data-pod="${esc(c.pod)}">
        <div class="contact-header">
            <div style="display:flex;align-items:center;gap:8px;">
                <input type="checkbox" class="privati-row-check" data-pod="${esc(c.pod)}" ${isSelected ? 'checked' : ''} onchange="onPrivatiRowCheckChange(this)" title="Seleziona per invio multiplo SMS">
                <span class="lavoro-num">Lavoro ${esc(c.lavoro)}</span>
            </div>
            <span class="type-badge ${c.tipo}">
                ${c.tipo === 'cellulare' ? '<i class="fas fa-mobile-alt"></i> Cellulare' :
                  c.tipo === 'fisso' ? '<i class="fas fa-phone"></i> Fisso' :
                  '<i class="fas fa-phone-slash"></i> No Tel'}
            </span>
        </div>

        <div class="contact-name-row">
            <div class="contact-name">${esc(nomeContatto)}</div>
            ${c.nome ? `
                <button class="btn-copy-name" onclick="copiaNominativoPrivato('${esc(c.pod)}', event)" title="Copia nominativo">
                    <i class="fas fa-copy"></i>
                </button>
            ` : ''}
        </div>

        <div class="contact-details">
            ${telDisplay ? `
                <div class="phone ${isErrato ? 'phone-invalid' : ''}" id="phoneDisplay_privato_${esc(c.pod)}">
                    <i class="fas fa-phone"></i> ${esc(telDisplay)}
                    ${(c.telefono && !c.telefono_filone) ? `
                        <button class="btn-add-to-filone" onclick="event.stopPropagation(); aggiungiTelFilonePrivato('${esc(c.pod)}', '${esc(c.telefono)}')" title="Aggiungi questo numero al filone">
                            <i class="fas fa-plus-circle"></i> Aggiungi al filone
                        </button>
                    ` : ''}
                </div>
            ` : ''}
            <div><i class="fas fa-map-marker-alt"></i> ${esc(c.indirizzo || '')} ${esc(c.civico || '')}</div>
            <div><i class="fas fa-city"></i> ${esc(c.cap || '')} ${esc(c.comune || '')}</div>
            ${renderDataInterventoCard(c)}
            <div><i class="fas fa-barcode"></i> POD: ${esc(c.pod || '')}</div>
            ${c.tipologia_contatore ? `<div><i class="fas fa-tachometer-alt"></i> Tipo: ${esc(c.tipologia_contatore)}</div>` : ''}
            ${c.posizione_contatore ? `<div><i class="fas fa-map-pin"></i> Posizione: ${esc(c.posizione_contatore)}</div>` : ''}
        </div>

        <div class="contact-actions">
            <button class="btn-soft" onclick="apriPrivatiMessageModal('${esc(c.pod)}')" style="width:100%;font-weight:700;padding:6px 12px;font-size:0.84rem;display:inline-flex;align-items:center;justify-content:center;gap:6px;border-radius:6px;${c.tipo === 'cellulare' ? 'color:#10b981;border-color:rgba(16,185,129,0.4);' : ''}">
                <i class="${c.tipo === 'cellulare' ? 'fab fa-whatsapp' : 'fas fa-paper-plane'}"></i>
                Template WA / SMS / Mail
            </button>
            ${c.tipo === 'senza_telefono' && c.numeri_alternativi && c.numeri_alternativi.length > 0 ? `
                <div class="numeri-alternativi-box">
                    <div class="numeri-alternativi-header">
                        <i class="fas fa-lightbulb"></i> Numeri trovati in altri campi:
                    </div>
                    ${c.numeri_alternativi.map(n => `
                        <div class="numero-alternativo">
                            <span class="numero-fonte">${
                                n.fonte === 'filone' ? 'Tel. Filone' :
                                n.fonte === 'nominativo_filone' ? 'Nel nome (Filone)' :
                                n.fonte === 'nominativo_distinta' ? 'Nel nome' :
                                n.fonte === 'indirizzo_filone' ? 'Indirizzo (Filone)' :
                                n.fonte === 'comune_filone' ? 'Comune (Filone)' :
                                n.fonte === 'note_filone' ? 'Note (Filone)' : n.fonte
                            }</span>
                            <span class="numero-valore ${n.tipo}">${esc(n.numero)}</span>
                            <span class="numero-tipo-badge ${n.tipo}"><i class="fas ${n.tipo === 'cellulare' ? 'fa-mobile-alt' : 'fa-phone'}"></i></span>
                            ${n.tipo === 'cellulare' ? `
                                <button type="button" class="btn-mini btn-whatsapp" onclick="event.stopPropagation(); apriPrivatiMessageModal('${esc(c.pod)}', '${esc(n.numero)}', 'cellulare')" title="Invia messaggio a questo numero">
                                    <i class="fab fa-whatsapp"></i>
                                </button>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
            ` : ''}
        </div>

        <div class="phone-edit-row" title="Modifica Telefono sul filone (premi Invio per salvare)">
            <i class="fas fa-phone phone-icon"></i>
            <input type="text" class="phone-edit-input ${c.telefono_filone ? 'has-phone' : ''}" id="phoneEdit_privato_${esc(c.pod)}"
                value="${esc(c.telefono_filone || '')}" placeholder="Nessun telefono sul filone"
                onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaTelefonoPrivato('${esc(c.pod)}', this.value);}"
                onblur="salvaTelefonoPrivato('${esc(c.pod)}', this.value)">
        </div>

        <div class="phone-edit-row" title="Modifica Email sul filone (premi Invio per salvare)">
            <i class="fas fa-envelope phone-icon" style="color: var(--accent, #3b82f6);"></i>
            <input type="text" class="phone-edit-input ${c.email_filone ? 'has-phone' : ''}" id="emailEdit_privato_${esc(c.pod)}"
                value="${esc(c.email_filone || '')}" placeholder="Nessuna email sul filone"
                onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaEmailPrivato('${esc(c.pod)}', this.value);}"
                onblur="salvaEmailPrivato('${esc(c.pod)}', this.value)">
        </div>

        <div class="checkbox-wrapper">
            <input type="checkbox" id="completed_privato_${esc(c.pod)}"
                ${isCompleted ? 'checked' : ''}
                onchange="toggleCompletedPrivato('${esc(c.pod)}')">
            <label for="completed_privato_${esc(c.pod)}">Completato</label>
        </div>

        <div class="note-actions">
            <input type="text" class="note-display ${note ? 'has-note' : ''}" id="noteDisplay_privato_${esc(c.pod)}"
                value="${esc(note)}" placeholder="Nessuna nota (premi Invio per salvare)"
                onkeydown="if(event.key==='Enter'){event.preventDefault(); salvaNotaPrivato('${esc(c.pod)}', this.value);}"
                onblur="salvaNotaPrivato('${esc(c.pod)}', this.value)">
            <div class="btn-add-note" onclick="toggleNoteDropdownPrivato('${esc(c.pod)}')">
                <i class="fas fa-plus"></i> Nota
                <div class="note-dropdown" id="noteDropdown_privato_${esc(c.pod)}">
                    <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'mandato sms')">
                        <i class="fas fa-sms" style="color: #10b981"></i> Mandato SMS
                    </div>
                    <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'mandato wa')">
                        <i class="fab fa-whatsapp" style="color: #25d366"></i> Mandato WA
                    </div>
                    <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'nr')">
                        <i class="fas fa-phone-slash" style="color: var(--danger, #ef4444)"></i> Non risponde
                    </div>
                    <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'chiamare')">
                        <i class="fas fa-phone" style="color: var(--info, #3b82f6)"></i> Chiamare
                    </div>
                    <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'numero errato')">
                        <i class="fas fa-times-circle" style="color: #ef4444"></i> ❌ Numero errato
                    </div>
                    <div class="note-dropdown-item" onclick="aggiungiNotaPrivato('${esc(c.pod)}', 'mandata mail')">
                        <i class="fas fa-envelope" style="color: var(--accent, #6366f1)"></i> Mandata mail
                    </div>
                </div>
            </div>
        </div>

        <div class="appuntamento-section">
            <div class="appuntamento-title">
                <i class="fas fa-calendar-alt"></i> Modifica Data / Fissa Appuntamento
            </div>
            <div class="appuntamento-date-row">
                <span class="appuntamento-date-label">Data:</span>
                <input type="date" class="appuntamento-date-input" id="appDate_privato_${esc(c.pod)}"
                    value="${formatDateForInput(c.data_pian_filone || c.data_lettera || c.data_intervento)}">
                <button type="button" class="btn-salva-data" onclick="salvaDataPrivato('${esc(c.pod)}')">
                    <i class="fas fa-save"></i> Salva data
                </button>
            </div>
            <div class="appuntamento-row">
                <span class="appuntamento-date-label">Orario:</span>
                <div class="appuntamento-time-group">
                    <input type="time" class="appuntamento-time-input" id="appStart_privato_${esc(c.pod)}" value="09:00">
                    <span>-</span>
                    <input type="time" class="appuntamento-time-input" id="appEnd_privato_${esc(c.pod)}" placeholder="--:--">
                </div>
                <button type="button" class="btn-appuntamento btn-fissa" onclick="fissaAppuntamentoPrivato('${esc(c.pod)}')">
                    <i class="fas fa-check"></i> Fissa
                </button>
                <button type="button" class="btn-appuntamento btn-rimuovi" id="btnRimuoviApp_privato_${esc(c.pod)}" onclick="rimuoviAppuntamentoPrivato('${esc(c.pod)}')" style="${(c.note_filone || '').toUpperCase().includes('APP') ? '' : 'display: none;'}">
                    <i class="fas fa-trash"></i> Rimuovi
                </button>
            </div>
            <div class="appuntamento-quick-btns">
                <span style="font-size:0.72rem;color:var(--text-muted);font-weight:600;">Regola rapida:</span>
                <button type="button" class="btn-quick-time" onclick="regolaOrarioPrivato('${esc(c.pod)}', 10)">+10m</button>
                <button type="button" class="btn-quick-time" onclick="regolaOrarioPrivato('${esc(c.pod)}', 15)">+15m</button>
                <button type="button" class="btn-quick-time" onclick="regolaOrarioPrivato('${esc(c.pod)}', 30)">+30m</button>
            </div>
        </div>
    </div>
    `;
}

function formatDateForInput(dateStr) {
    if (!dateStr) return '';
    const clean = String(dateStr).trim();
    const parts = clean.split('/');
    if (parts.length === 3) {
        return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
    }
    return clean;
}

function formatDateToItalian(dateStr) {
    if (!dateStr) return '';
    const clean = String(dateStr).trim();
    const parts = clean.split('-');
    if (parts.length === 3) {
        return `${parts[2]}/${parts[1]}/${parts[0]}`;
    }
    return clean;
}

function formatDataBreve(dateStr) {
    if (!dateStr) return '';
    const val = String(dateStr).trim();
    let m = val.match(/(\d{1,2})\/(\d{1,2})(?:\/\d{2,4})?/);
    if (m) return `${m[1].padStart(2, '0')}/${m[2].padStart(2, '0')}`;
    m = val.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (m) return `${m[3]}/${m[2]}`;
    return val;
}

function toggleCompletedPrivato(pod) {
    completedPrivati[pod] = !completedPrivati[pod];
    localStorage.setItem('lettere_privati_completed', JSON.stringify(completedPrivati));
    renderPrivati();
}

function copiaNominativoPrivato(pod, ev) {
    if (ev) ev.stopPropagation();
    const c = privati.find(x => x.pod === pod);
    if (!c || !c.nome) return;
    navigator.clipboard.writeText(c.nome)
        .then(() => showToast(`Copiato: ${c.nome}`))
        .catch(() => showToast('Errore copia negli appunti', true));
}

function toggleNoteDropdownPrivato(pod) {
    const el = document.getElementById(`noteDropdown_privato_${pod}`);
    if (el) {
        const isOpen = el.classList.contains('show');
        document.querySelectorAll('.note-dropdown.show').forEach(d => d.classList.remove('show'));
        if (!isOpen) el.classList.add('show');
    }
}

async function aggiungiNotaPrivato(pod, testo, opzioni) {
    opzioni = opzioni || {};
    document.querySelectorAll('.note-dropdown.show').forEach(d => d.classList.remove('show'));
    const inputCard = document.getElementById(`noteDisplay_privato_${pod}`);
    const inputTable = document.getElementById(`noteTable_privato_${pod}`);
    if (testo === 'numero errato') {
        erratiPrivati[pod] = true;
        localStorage.setItem('lettere_privati_errati', JSON.stringify(erratiPrivati));
    }
    const c = privati.find(x => x.pod === pod);

    const oggi = new Date();
    const gg = String(oggi.getDate()).padStart(2, '0');
    const mm = String(oggi.getMonth() + 1).padStart(2, '0');
    const prefisso = `${gg}/${mm}`;

    const tLower = (testo || '').toLowerCase().trim();
    const isMail = tLower === 'mandata mail' || tLower === 'mail';
    const isWa = tLower === 'mandato wa' || tLower === 'mandato msg wa' || tLower === 'mandato whatsapp' || tLower === 'wa';

    let testoNota = testo;
    if (tLower === 'chiamare') {
        testoNota = 'chiamare';
    } else if (isMail || isWa) {
        const azione = isMail ? 'mandata mail' : 'mandato wa';
        const dateInput = document.getElementById(`appDate_privato_${pod}`);
        const dateVal = (dateInput && dateInput.value) || opzioni.dataIntervento || (c && (c.data_lettera || c.data_intervento || c.data_pian_filone)) || '';
        const dataBreve = formatDataBreve(dateVal);
        testoNota = dataBreve ? `${prefisso} ${azione} per ${dataBreve}` : `${prefisso} ${azione}`;
    } else if (!testo.startsWith(prefisso) && !testo.match(/^\d{1,2}\/\d{1,2}/)) {
        testoNota = `${prefisso} ${testo}`;
    }

    let current = (noteStatesPrivati[pod] || (c ? (c.note_filone || c.note || c.note_appuntamento || '') : '') || (inputCard ? inputCard.value : (inputTable ? inputTable.value : '')) || '').trim();
    if (current && current.includes(testoNota)) {
        showToast('Nota già presente');
        return;
    }
    let nuovaNota = current ? `${testoNota} // ${current}` : testoNota;
    if (inputCard) inputCard.value = nuovaNota;
    if (inputTable) inputTable.value = nuovaNota;
    await salvaNotaPrivato(pod, nuovaNota);
}

async function salvaNotaPrivato(pod, nota) {
    const cleanNota = String(nota || '').trim();
    noteStatesPrivati[pod] = cleanNota;
    const c = privati.find(x => x.pod === pod);
    if (c) {
        c.note_filone = cleanNota;
        c.note = cleanNota;
        c.note_appuntamento = cleanNota;
    }

    const inputCard = document.getElementById(`noteDisplay_privato_${pod}`);
    if (inputCard) inputCard.value = cleanNota;
    const inputTable = document.getElementById(`noteTable_privato_${pod}`);
    if (inputTable) inputTable.value = cleanNota;

    try {
        const res = await fetch('/api/lettere/salva-nota', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pod,
                lcl: c ? (c.lcl || '') : '',
                eneltel: c ? (c.eneltel || c.eneltel_filone || '') : '',
                nota: cleanNota,
                sovrascrivere: true
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Nota salvata sul filone');
        } else {
            showToast(data.error || 'Errore salvataggio nota', true);
        }
    } catch (e) {
        showToast(e.message || 'Errore salvataggio nota', true);
    }
}

async function salvaTelefonoPrivato(pod, telefono) {
    const cleanTel = String(telefono || '').trim();
    const c = privati.find(x => x.pod === pod);
    if (c) {
        if (c.telefono_filone === cleanTel) return;
        c.telefono_filone = cleanTel;
    }

    const inputCard = document.getElementById(`phoneEdit_privato_${pod}`);
    if (inputCard) inputCard.value = cleanTel;
    const inputTable = document.getElementById(`phoneTable_privato_${pod}`);
    if (inputTable) inputTable.value = cleanTel;

    try {
        const res = await fetch('/api/lettere/salva-telefono', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pod, lcl: c ? (c.lcl || '') : '', telefono: cleanTel })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Telefono salvato sul filone');
        } else {
            showToast(data.error || 'Errore salvataggio telefono', true);
        }
    } catch (e) {
        showToast(e.message || 'Errore salvataggio telefono', true);
    }
}

async function salvaEmailPrivato(pod, email) {
    const cleanEmail = String(email || '').trim();
    const c = privati.find(x => x.pod === pod);
    if (c) {
        if (c.email_filone === cleanEmail) return;
        c.email_filone = cleanEmail;
    }

    const inputCard = document.getElementById(`emailEdit_privato_${pod}`);
    if (inputCard) inputCard.value = cleanEmail;
    const inputTable = document.getElementById(`emailTable_privato_${pod}`);
    if (inputTable) inputTable.value = cleanEmail;

    try {
        const res = await fetch('/api/lettere/salva-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pod, lcl: c ? (c.lcl || '') : '', email: cleanEmail })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Email salvata sul filone');
        } else {
            showToast(data.error || 'Errore salvataggio email', true);
        }
    } catch (e) {
        showToast(e.message || 'Errore salvataggio email', true);
    }
}

async function aggiungiTelFilonePrivato(pod, tel) {
    const inputCard = document.getElementById(`phoneEdit_privato_${pod}`);
    if (inputCard) inputCard.value = tel;
    const inputTable = document.getElementById(`phoneTable_privato_${pod}`);
    if (inputTable) inputTable.value = tel;
    await salvaTelefonoPrivato(pod, tel);
}

async function salvaDataPrivato(pod) {
    const dateInput = document.getElementById(`appDate_privato_${pod}`);
    if (!dateInput || !dateInput.value) {
        showToast('Seleziona una data valida', true);
        return;
    }
    const dataItaliana = formatDateToItalian(dateInput.value);
    try {
        const res = await fetch('/api/lettere/aggiorna-data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pod, data: dataItaliana })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Data aggiornata: ${dataItaliana}`);
            const c = privati.find(x => x.pod === pod);
            if (c) {
                c.data_pian_filone = dataItaliana;
                renderPrivati();
            }
        } else {
            showToast(data.error || 'Errore aggiornamento data', true);
        }
    } catch (e) {
        showToast(e.message || 'Errore aggiornamento data', true);
    }
}

async function fissaAppuntamentoPrivato(pod) {
    const start = document.getElementById(`appStart_privato_${pod}`)?.value || '';
    const end = document.getElementById(`appEnd_privato_${pod}`)?.value || '';
    const orario = end ? `${start}-${end}` : start;
    const dateVal = document.getElementById(`appDate_privato_${pod}`)?.value || '';
    const dataItaliana = formatDateToItalian(dateVal);

    if (!orario) {
        showToast('Inserisci un orario per l\'appuntamento', true);
        return;
    }

    const c = privati.find(x => x.pod === pod);
    const inputCard = document.getElementById(`noteDisplay_privato_${pod}`);
    const inputTable = document.getElementById(`noteTable_privato_${pod}`);
    const currentNote = (noteStatesPrivati[pod] !== undefined)
        ? noteStatesPrivati[pod]
        : ((inputCard ? inputCard.value : (inputTable ? inputTable.value : '')) || (c ? (c.note_filone || c.note || c.note_appuntamento || '') : ''));

    try {
        const res = await fetch('/api/lettere/appuntamento', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pod,
                lcl: c ? (c.lcl || '') : '',
                eneltel: c ? (c.eneltel || c.eneltel_filone || '') : '',
                orario,
                data_appuntamento: dataItaliana,
                nota_corrente: currentNote
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || 'Appuntamento impostato');
            const btnRim = document.getElementById(`btnRimuoviApp_privato_${pod}`);
            if (btnRim) btnRim.style.display = 'inline-flex';
            if (data.nota !== undefined) {
                noteStatesPrivati[pod] = data.nota;
                if (c) {
                    c.note_filone = data.nota;
                    c.note = data.nota;
                    c.note_appuntamento = data.nota;
                }
                const noteCard = document.getElementById(`noteDisplay_privato_${pod}`);
                if (noteCard) {
                    noteCard.value = data.nota;
                    noteCard.classList.toggle('has-note', Boolean(data.nota));
                }
                const noteTable = document.getElementById(`noteTable_privato_${pod}`);
                if (noteTable) {
                    noteTable.value = data.nota;
                    noteTable.classList.toggle('has-note', Boolean(data.nota));
                }
            }
        } else {
            showToast(data.error || 'Errore appuntamento', true);
        }
    } catch (e) {
        showToast(e.message || 'Errore impostazione appuntamento', true);
    }
}

async function rimuoviAppuntamentoPrivato(pod) {
    if (!confirm('Rimuovere l\'appuntamento da questo contatore?')) return;
    const c = privati.find(x => x.pod === pod);
    const inputCard = document.getElementById(`noteDisplay_privato_${pod}`);
    const inputTable = document.getElementById(`noteTable_privato_${pod}`);
    const currentNote = (noteStatesPrivati[pod] !== undefined)
        ? noteStatesPrivati[pod]
        : ((inputCard ? inputCard.value : (inputTable ? inputTable.value : '')) || (c ? (c.note_filone || c.note || c.note_appuntamento || '') : ''));

    try {
        const res = await fetch('/api/lettere/rimuovi-appuntamento', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pod,
                lcl: c ? (c.lcl || '') : '',
                eneltel: c ? (c.eneltel || c.eneltel_filone || '') : '',
                nota_corrente: currentNote
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Appuntamento rimosso');
            const btnRim = document.getElementById(`btnRimuoviApp_privato_${pod}`);
            if (btnRim) btnRim.style.display = 'none';
            if (data.nota !== undefined) {
                noteStatesPrivati[pod] = data.nota;
                if (c) {
                    c.note_filone = data.nota;
                    c.note = data.nota;
                    c.note_appuntamento = data.nota;
                }
                const noteCard = document.getElementById(`noteDisplay_privato_${pod}`);
                if (noteCard) {
                    noteCard.value = data.nota;
                    noteCard.classList.toggle('has-note', Boolean(data.nota));
                }
                const noteTable = document.getElementById(`noteTable_privato_${pod}`);
                if (noteTable) {
                    noteTable.value = data.nota;
                    noteTable.classList.toggle('has-note', Boolean(data.nota));
                }
            }
        } else {
            showToast(data.error || 'Errore rimozione appuntamento', true);
        }
    } catch (e) {
        showToast(e.message || 'Errore rimozione appuntamento', true);
    }
}

function regolaOrarioPrivato(pod, deltaMinuti) {
    const startInput = document.getElementById(`appStart_privato_${pod}`);
    const endInput = document.getElementById(`appEnd_privato_${pod}`);
    if (!startInput) return;
    let [h, m] = (startInput.value || '09:00').split(':').map(Number);
    let totMin = h * 60 + m + deltaMinuti;
    if (totMin < 0) totMin = 0;
    if (totMin >= 24 * 60) totMin = 24 * 60 - 1;
    const nh = String(Math.floor(totMin / 60)).padStart(2, '0');
    const nm = String(totMin % 60).padStart(2, '0');
    startInput.value = `${nh}:${nm}`;
    if (endInput && !endInput.value) {
        let totEnd = totMin + 120;
        if (totEnd >= 24 * 60) totEnd = 24 * 60 - 1;
        const neh = String(Math.floor(totEnd / 60)).padStart(2, '0');
        const nem = String(totEnd % 60).padStart(2, '0');
        endInput.value = `${neh}:${nem}`;
    }
}

/* ==========================================================================
   MODALE ANTEPRIMA SINGOLO MESSAGGIO / TEMPLATE PER PRIVATI (CENTRALIZZATO)
   ========================================================================== */
function apriPrivatiMessageModal(pod, telOverride, tipoOverride) {
    const c = privati.find(x => x.pod === pod);
    if (!c) {
        showToast('Contatto non trovato', true);
        return;
    }
    const contactCopy = { ...c };
    const emailInput = document.getElementById(`emailEdit_privato_${pod}`) || document.getElementById(`emailTable_privato_${pod}`);
    if (emailInput && emailInput.value) {
        contactCopy.email = emailInput.value.trim();
        contactCopy.email_filone = emailInput.value.trim();
    } else if (!contactCopy.email && contactCopy.email_filone) {
        contactCopy.email = contactCopy.email_filone;
    }
    if (window.MessageTemplateModal) {
        window.MessageTemplateModal.open({
            contact: contactCopy,
            telOverride: telOverride,
            tipoOverride: tipoOverride,
            smsEndpoint: '/api/lettere/invia-singolo-sms',
            emailEndpoint: '/api/distinte/invia-singola-email',
            context: 'lettere_privati',
            onSmsSent: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_intervento || contact.data_pian_filone)) || c.data_pian_filone || c.data_intervento || '';
                const notaInvio = `mandato SMS per ${dataIntervento}`;
                await aggiungiNotaPrivato(p, notaInvio, { dataIntervento });
            },
            onEmailSent: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_intervento || contact.data_pian_filone)) || c.data_pian_filone || c.data_intervento || '';
                await aggiungiNotaPrivato(p, 'mandata mail', { dataIntervento });
            },
            onWaOpened: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_intervento || contact.data_pian_filone)) || c.data_pian_filone || c.data_intervento || '';
                await aggiungiNotaPrivato(p, 'mandato wa', { dataIntervento });
            },
            onMailOpened: async (p, contact) => {
                const dataIntervento = (contact && (contact.data_intervento || contact.data_pian_filone)) || c.data_pian_filone || c.data_intervento || '';
                await aggiungiNotaPrivato(p, 'mandata mail', { dataIntervento });
            }
        });
    } else {
        showToast('Modulo template non ancora caricato', true);
    }
}

/* ==========================================================================
   MODALE INVIO MULTIPLO SMS A UTENTI PRIVATI
   ========================================================================== */
function estraiMigliorCellularePrivato(c) {
    if (c.tipo === 'cellulare' && (c.telefono || '').trim()) {
        return c.telefono.trim();
    }
    if (c.numeri_alternativi && c.numeri_alternativi.length > 0) {
        const alt = c.numeri_alternativi.find(n => n.tipo === 'cellulare' && (n.numero || '').trim());
        if (alt) return alt.numero.trim();
    }
    return (c.telefono || c.telefono_filone || '').trim();
}

async function apriPrivatiBulkSmsModal() {
    if (!privati || !privati.length) {
        showToast('Nessun utente privato disponibile', true);
        return;
    }

    if (selectedPrivatiPods.size > 0) {
        bulkSmsContacts = privati.filter(c => selectedPrivatiPods.has(c.pod) && Boolean(estraiMigliorCellularePrivato(c)));
        if (!bulkSmsContacts.length) {
            showToast('Nessuno dei contatti selezionati ha un numero di cellulare', true);
            return;
        }
    } else {
        bulkSmsContacts = privati.filter(c => {
            if (c.tipo === 'cellulare' && (c.telefono || '').trim()) return true;
            if (c.numeri_alternativi && c.numeri_alternativi.some(n => n.tipo === 'cellulare' && (n.numero || '').trim())) return true;
            return false;
        });

        if (!bulkSmsContacts.length) {
            showToast('Nessun contatto con numero cellulare disponibile', true);
            return;
        }
    }

    const select = document.getElementById('privatiBulkSmsTemplateSelect');
    if (select) {
        select.innerHTML = '';
        let selectedTmplId = '';
        for (const [id, tmpl] of Object.entries(TEMPLATES || {})) {
            if (tmpl.type === 'sms' || tmpl.type === 'whatsapp') {
                const opt = document.createElement('option');
                opt.value = id;
                const isSms = tmpl.type === 'sms';
                opt.textContent = (isSms ? '📱 ' : '💬 ') + (tmpl.label || id);
                select.appendChild(opt);
                if (!selectedTmplId && (id === 'sms_preavviso' || id === 'wa_conferma')) {
                    selectedTmplId = id;
                }
            }
        }
        const optCustom = document.createElement('option');
        optCustom.value = 'custom';
        optCustom.textContent = '✏️ Testo personalizzato...';
        select.appendChild(optCustom);

        if (selectedTmplId) select.value = selectedTmplId;
        else if (select.options.length > 0) select.selectedIndex = 0;
    }

    onPrivatiBulkSmsTemplateChange();
    await caricaStatoPrivatiSmsGateway();
    popolaPrivatiBulkSmsContactsList(bulkSmsContacts);

    const progressEl = document.getElementById('privatiBulkSmsProgress');
    if (progressEl) progressEl.style.display = 'none';
    const sendBtn = document.getElementById('privatiBulkSmsSendBtn');
    if (sendBtn) {
        sendBtn.innerHTML = '<i class="fas fa-sms"></i> Invia SMS Selezionati';
        sendBtn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
        sendBtn.onclick = inviaPrivatiSmsBulk;
    }

    const modal = document.getElementById('privatiBulkSmsModal');
    if (modal) modal.classList.add('active');
}

function chiudiPrivatiBulkSmsModal() {
    const modal = document.getElementById('privatiBulkSmsModal');
    if (modal) modal.classList.remove('active');
}

async function caricaStatoPrivatiSmsGateway() {
    const statusDiv = document.getElementById('privatiBulkSmsStatusText');
    const delayInput = document.getElementById('privatiBulkSmsDelaySec');
    const sendBtn = document.getElementById('privatiBulkSmsSendBtn');

    try {
        const resp = await fetch('/api/lettere/sms-settings');
        const data = await resp.json();
        if (data.ok) {
            currentSmsSettings = data;
            const enabled = data.enabled !== false;
            const configured = Boolean(data.endpoint_url);
            const isReady = enabled && configured;

            if (data.rate_limit_delay && delayInput) {
                delayInput.value = data.rate_limit_delay;
            }

            if (!isReady) {
                if (statusDiv) {
                    statusDiv.innerHTML = `
                        <span style="color:#f59e0b;font-weight:700;"><i class="fas fa-exclamation-triangle"></i> Gateway SMS non configurato o disabilitato</span>
                        <button type="button" class="btn-preset" onclick="apriDistinteSmsConfigModal()" style="font-size:0.75rem;padding:3px 8px;margin-left:10px;">Configura Ora</button>
                    `;
                }
                if (sendBtn) {
                    sendBtn.disabled = true;
                    sendBtn.style.opacity = '0.6';
                    sendBtn.title = 'Configura il gateway SMS prima di inviare i messaggi';
                }
                return;
            }

            if (sendBtn) {
                sendBtn.disabled = false;
                sendBtn.style.opacity = '1';
                sendBtn.title = '';
            }
            if (statusDiv) {
                const prov = data.provider || 'android_sms_gateway';
                statusDiv.innerHTML = `
                    <span class="badge" style="background:#2563eb;color:white;font-weight:600;padding:3px 8px;border-radius:4px;">
                        <i class="fas ${prov === 'android_sms_gateway' ? 'fa-mobile-alt' : 'fa-network-wired'}"></i>
                        ${prov === 'android_sms_gateway' ? 'Android SIM Gateway' : 'Webhook Gateway'}
                    </span>
                    <span style="color:var(--text-muted);font-size:0.8rem;margin-left:6px;">Gateway pronto all'invio</span>
                `;
            }
        }
    } catch (e) {
        if (statusDiv) statusDiv.innerHTML = `<span style="color:#ef4444;"><i class="fas fa-times-circle"></i> Errore verifica SMS Gateway</span>`;
    }
}

function onPrivatiBulkSmsTemplateChange() {
    const select = document.getElementById('privatiBulkSmsTemplateSelect');
    const tmplId = select ? select.value : '';
    const textarea = document.getElementById('privatiBulkSmsMessageText');
    if (!textarea) return;

    if (tmplId === 'custom') {
        textarea.focus();
        return;
    }

    const tmpl = (TEMPLATES || {})[tmplId];
    if (tmpl && tmpl.body) {
        let body = tmpl.body
            .replace(/<br\s*[\/]?>/gi, '\n')
            .replace(/<\/p>/gi, '\n\n')
            .replace(/<[^>]+>/gi, '')
            .replace(/\*([^*]+)\*/g, '$1');
        textarea.value = body.trim();
    }
    aggiornaPrivatiSmsCharCounter();
}

function aggiornaPrivatiSmsCharCounter() {
    const text = document.getElementById('privatiBulkSmsMessageText')?.value || '';
    const len = text.length;

    const gsm7Regex = /^[@£$¥èéùìòÇ\r\nØø\u001fÅåΔ_ΦΓΛΩΠΨΣΘΞ^{}\\[~\]|€ÆæßÉ !"#%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà]*$/;
    const isGsm7 = gsm7Regex.test(text);

    let parts = 0;
    let maxSingle = isGsm7 ? 160 : 70;
    let maxConcat = isGsm7 ? 153 : 67;

    if (len === 0) {
        parts = 0;
    } else if (len <= maxSingle) {
        parts = 1;
    } else {
        parts = Math.ceil(len / maxConcat);
    }

    const counterEl = document.getElementById('privatiBulkSmsCharCount');
    const encEl = document.getElementById('privatiBulkSmsEncodingNote');
    if (counterEl) {
        counterEl.textContent = `${len} car | ${parts} SMS (${isGsm7 ? 'GSM-7' : 'Unicode'})`;
        counterEl.style.color = parts <= 1 ? '#10b981' : (parts === 2 ? '#f59e0b' : '#ef4444');
    }
    if (encEl) {
        encEl.textContent = isGsm7
            ? `GSM-7 (160 car/SMS, max ${maxConcat} per concatenato)`
            : `⚠️ Unicode (emoji/caratteri speciali: max 70 car/SMS)`;
        encEl.style.color = isGsm7 ? 'var(--text-muted)' : '#f59e0b';
    }
}

function popolaPrivatiBulkSmsContactsList(contattiCell) {
    const list = document.getElementById('privatiBulkSmsContactsList');
    if (!list) return;

    if (!contattiCell || !contattiCell.length) {
        list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">Nessun contatto cellulare disponibile.</div>';
        const cntEl = document.getElementById('privatiBulkSmsCount');
        if (cntEl) cntEl.textContent = '0 contatti';
        return;
    }

    list.innerHTML = contattiCell.map((c, i) => {
        const tel = estraiMigliorCellularePrivato(c);
        const tipoCe = (c.tipologia_contatore || c.tipo_misuratore || '').trim();
        const tipoStr = tipoCe.toLowerCase();
        let tipoBadge = '';
        if (tipoStr.includes('semidirett')) {
            tipoBadge = `<span class="badge" style="background:#8b5cf6;font-size:0.65rem;color:white;">SEMIDIRETTA</span>`;
        } else if (tipoStr.includes('tri')) {
            tipoBadge = `<span class="badge" style="background:#3b82f6;font-size:0.65rem;color:white;">TRIFASE</span>`;
        } else if (tipoStr.includes('mono')) {
            tipoBadge = `<span class="badge" style="background:#10b981;font-size:0.65rem;color:white;">MONOFASE</span>`;
        }

        const nota = noteStatesPrivati[c.pod] || c.note_filone || '';
        const notaHtml = nota ? `
            <div style="color:#f59e0b;font-size:0.78rem;display:flex;align-items:center;gap:5px;margin-top:2px;">
                <i class="fas fa-sticky-note"></i> <span>${esc(nota)}</span>
            </div>
        ` : '';

        return `
        <div class="bulk-contact-item" style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 10px;border-bottom:1px solid var(--border);">
            <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0;">
                <input type="checkbox" class="privati-bulk-sms-check" data-pod="${esc(c.pod)}" data-tel="${esc(tel)}" data-nome="${esc(c.nome || '')}" data-tipoce="${esc(tipoCe)}" data-hasnote="${Boolean(nota)}" data-idx="${i}" checked onchange="aggiornaPrivatiSmsConteggi()">
                <div style="min-width:0;flex:1;">
                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                        <strong style="font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(c.nome || 'N/D')}</strong>
                        ${tipoBadge}
                    </div>
                    <div style="font-size:0.75rem;color:var(--text-muted);">
                        ${esc(c.comune || '')} ${esc(c.indirizzo || '')} ${esc(c.civico || '')}
                    </div>
                    ${notaHtml}
                </div>
            </div>
            <div style="text-align:right;">
                <span style="font-family:monospace;font-size:0.85rem;font-weight:700;color:#10b981;">
                    <i class="fas fa-phone-alt" style="font-size:0.75rem;"></i> ${esc(tel)}
                </span>
                <div style="font-size:0.75rem;color:var(--text-muted);">${esc(c.pod || '')}</div>
            </div>
        </div>
        `;
    }).join('');

    const countEl = document.getElementById('privatiBulkSmsCount');
    if (countEl) countEl.textContent = `${contattiCell.length} contatti`;
    aggiornaPrivatiSmsConteggi();
}

function togglePrivatiBulkSmsSelectAll(checked) {
    document.querySelectorAll('.privati-bulk-sms-check').forEach(cb => {
        cb.checked = checked;
        const item = cb.closest('.bulk-contact-item');
        if (item) item.style.opacity = checked ? '' : '0.45';
    });
    aggiornaPrivatiSmsConteggi();
}

function applyPrivatiSmsPreset(preset) {
    document.querySelectorAll('#privatiSmsPresets .btn-preset').forEach(b => b.classList.remove('active'));
    const activeBtn = document.getElementById(`privatiSmsPreset_${preset}`);
    if (activeBtn) activeBtn.classList.add('active');

    const checks = document.querySelectorAll('.privati-bulk-sms-check');
    checks.forEach(cb => {
        const tipo = (cb.dataset.tipoce || '').toLowerCase();
        const hasNote = cb.dataset.hasnote === 'true';

        if (preset === 'tutti') cb.checked = true;
        else if (preset === 'monofase') cb.checked = tipo.includes('mono');
        else if (preset === 'trifase') cb.checked = tipo.includes('tri') && !tipo.includes('semidirett');
        else if (preset === 'con_note') cb.checked = hasNote;
        else if (preset === 'senza_note') cb.checked = !hasNote;

        const item = cb.closest('.bulk-contact-item');
        if (item) item.style.opacity = cb.checked ? '' : '0.45';
    });
    aggiornaPrivatiSmsConteggi();
}

function aggiornaPrivatiSmsConteggi() {
    const checks = document.querySelectorAll('.privati-bulk-sms-check');
    const checked = document.querySelectorAll('.privati-bulk-sms-check:checked');
    const total = checks.length;
    const checkedCount = checked.length;
    const allCb = document.getElementById('privatiBulkSmsSelectAll');
    if (allCb) allCb.checked = checkedCount === total && total > 0;
    const cntEl = document.getElementById('privatiBulkSmsCount');
    if (cntEl) cntEl.textContent = `${checkedCount} di ${total} selezionati`;
}

async function inviaPrivatiSmsBulk() {
    const checkedBoxes = Array.from(document.querySelectorAll('.privati-bulk-sms-check:checked'));
    if (!checkedBoxes.length) {
        showToast('Seleziona almeno un destinatario', true);
        return;
    }

    const rawText = document.getElementById('privatiBulkSmsMessageText')?.value.trim();
    if (!rawText) {
        showToast('Inserisci il testo del messaggio SMS', true);
        return;
    }

    const delaySec = Math.max(1, parseInt(document.getElementById('privatiBulkSmsDelaySec')?.value) || 4);
    const tempoStimatoSec = checkedBoxes.length * delaySec;
    const tempoStimatoMin = Math.ceil(tempoStimatoSec / 60);

    if (!confirm(`Stai per inviare SMS a ${checkedBoxes.length} destinatari.\n\nPausa anti-flood: ${delaySec}s tra ogni invio.\nTempo stimato: ~${tempoStimatoMin} min.\n\nVuoi procedere?`)) {
        return;
    }

    bulkSmsAbortato = false;
    const progressEl = document.getElementById('privatiBulkSmsProgress');
    const progressFill = document.getElementById('privatiBulkSmsProgressFill');
    const progressText = document.getElementById('privatiBulkSmsProgressText');
    const logEl = document.getElementById('privatiBulkSmsLog');
    const sendBtn = document.getElementById('privatiBulkSmsSendBtn');

    if (progressEl) progressEl.style.display = 'block';
    if (logEl) logEl.innerHTML = '';
    if (sendBtn) {
        sendBtn.innerHTML = '<i class="fas fa-stop"></i> Interrompi';
        sendBtn.style.background = '#ef4444';
        sendBtn.onclick = () => { bulkSmsAbortato = true; };
    }

    let inviati = 0;
    let errori = 0;

    const pk = (window.provincia || 'TORINO').toUpperCase();
    const provSettings = (templateSettings.province && templateSettings.province[pk]) || {};
    const telTecnico = provSettings.telefono || templateSettings.telefono_tecnico || '011 025 2702';
    const emailTecnico = provSettings.email || templateSettings.email_tecnico || 'info@algecopower.it';

    for (let i = 0; i < checkedBoxes.length; i++) {
        if (bulkSmsAbortato) {
            if (logEl) {
                logEl.innerHTML += `<div style="color:#f59e0b;padding:3px 0;"><i class="fas fa-hand-paper"></i> <strong>Invio interrotto dall'operatore dopo ${inviati} messaggi.</strong></div>`;
                logEl.scrollTop = logEl.scrollHeight;
            }
            break;
        }

        const cb = checkedBoxes[i];
        const pod = cb.dataset.pod;
        const tel = cb.dataset.tel;
        const c = privati.find(x => x.pod === pod) || {};
        const nome = c.nome || cb.dataset.nome || 'Cliente';

        const dataIntervento = c.data_pian_filone || c.data_intervento || '';
        const comune = c.comune || '';
        const indirizzo = `${c.indirizzo || c.via || ''} ${c.civico || ''}`.trim();
        const operatore = c.operatore || c.matricola_operatore || '';

        const msgText = rawText
            .replace(/{nome}/g, nome)
            .replace(/{data}/g, dataIntervento)
            .replace(/{comune}/g, comune)
            .replace(/{indirizzo}/g, indirizzo)
            .replace(/{pod}/g, pod)
            .replace(/{nome_operatore}/g, operatore)
            .replace(/{telefono_tecnico}/g, telTecnico)
            .replace(/{email_tecnico}/g, emailTecnico);

        if (progressText) {
            progressText.textContent = `${i + 1} / ${checkedBoxes.length} — Invio a ${nome} (${tel})...`;
        }
        if (progressFill) {
            progressFill.style.width = `${Math.round(((i + 1) / checkedBoxes.length) * 100)}%`;
        }

        try {
            const resp = await fetch('/api/lettere/invia-singolo-sms', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    pod,
                    lcl: c ? (c.lcl || '') : '',
                    eneltel: c ? (c.eneltel || c.eneltel_filone || '') : '',
                    telefono: tel,
                    testo: msgText,
                    nome,
                    data_pian_filone: dataIntervento
                })
            });
            const res = await resp.json();

            if (res.ok) {
                inviati++;
                const parts = res.parts || res.parti_sms || 1;
                if (logEl) {
                    logEl.innerHTML += `<div style="color:#10b981;padding:2px 0;"><i class="fas fa-check-circle"></i> <strong>[SMS]</strong> Inviato a <b>${esc(nome)}</b> (${esc(tel)}) — ${parts} SMS</div>`;
                }
                // Registra nota locale preservando note esistenti
                const existing = (c ? (c.note_filone || c.note || '') : '') || noteStatesPrivati[pod] || '';
                const notaInvio = `mandato SMS per ${dataIntervento}`;
                const notaFinale = existing ? (existing.includes(notaInvio) ? existing : `${existing} // ${notaInvio}`) : notaInvio;
                noteStatesPrivati[pod] = notaFinale;
                if (c) {
                    c.note_filone = notaFinale;
                    c.note = notaFinale;
                    c.note_appuntamento = notaFinale;
                }
                const noteInput = document.getElementById(`noteDisplay_privato_${pod}`);
                if (noteInput) noteInput.value = notaFinale;
                const noteTable = document.getElementById(`noteTable_privato_${pod}`);
                if (noteTable) noteTable.value = notaFinale;
            } else {
                errori++;
                if (logEl) {
                    logEl.innerHTML += `<div style="color:#ef4444;padding:2px 0;"><i class="fas fa-times-circle"></i> <strong>[ERRORE]</strong> <b>${esc(nome)}</b> (${esc(tel)}): ${esc(res.error || res.errore || 'Errore di invio')}</div>`;
                }
            }
        } catch (e) {
            errori++;
            if (logEl) {
                logEl.innerHTML += `<div style="color:#ef4444;padding:2px 0;"><i class="fas fa-times-circle"></i> <strong>[ERRORE DI RETE]</strong> <b>${esc(nome)}</b>: ${esc(e.message)}</div>`;
            }
        }

        if (logEl) logEl.scrollTop = logEl.scrollHeight;

        if (i < checkedBoxes.length - 1 && !bulkSmsAbortato) {
            const targetTime = Date.now() + (delaySec * 1000);
            while (!bulkSmsAbortato) {
                const remainingMs = targetTime - Date.now();
                if (remainingMs <= 0) break;
                const sLeft = Math.ceil(remainingMs / 1000);
                if (progressText) {
                    progressText.textContent = `${i + 1} / ${checkedBoxes.length} — Attesa anti-flood (${sLeft}s)...`;
                }
                const waitMs = Math.min(1000, Math.max(100, remainingMs));
                await new Promise(r => setTimeout(r, waitMs));
            }
        }
    }

    if (sendBtn) {
        sendBtn.innerHTML = '<i class="fas fa-sms"></i> Invia SMS Selezionati';
        sendBtn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
        sendBtn.onclick = inviaPrivatiSmsBulk;
    }

    showToast(`Invio SMS completato: ${inviati} inviati${errori ? ', ' + errori + ' errori' : ''}`, Boolean(errori));
    renderPrivati();
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('searchInput')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') loadAziende();
    });
    document.getElementById('searchPrivatiInput')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') renderPrivati();
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
        if (document.getElementById('researchModal')?.classList.contains('active')) {
            closeContactResearch();
        } else if (document.getElementById('bulkModal')?.classList.contains('active')) {
            closeBulkModal();
        } else if (document.getElementById('privatiBulkSmsModal')?.classList.contains('active')) {
            chiudiPrivatiBulkSmsModal();
        } else if (document.getElementById('privatiMessageModal')?.classList.contains('active')) {
            chiudiPrivatiMessageModal();
        }
    });
    document.addEventListener('click', e => {
        if (!e.target.closest('.btn-add-note')) {
            document.querySelectorAll('.note-dropdown.show, .note-dropdown[style*="display: block"]').forEach(d => {
                d.classList.remove('show');
                d.style.display = 'none';
            });
        }
    });
    loadAll().catch(e => showToast(e.message, true));
});

