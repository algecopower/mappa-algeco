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
    await loadAziende();
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
    if (filterRecapitoVal === 'incompleti') {
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
        body.innerHTML = '<tr><td colspan="6" class="empty">Nessuna azienda trovata per il filtro selezionato</td></tr>';
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
    const emails = splitEmails(a.email);
    const missing = hasMissingContact(a);
    const rowClass = [
        missing ? 'incomplete-row' : '',
        checked ? 'selected' : '',
        a.risposta ? 'row-risposta' : '',
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
                    ${missing ? '<span class="badge da-completare" title="Recapiti mancanti">Da completare</span>' : ''}
                </div>
                <div class="azienda-sub">
                    <span>${esc(a.pod || '')}</span> ·
                    <span>${esc(a.comune || '')}</span>
                    ${a.via ? `· <span>${esc(a.via)} ${esc(a.civico || '')}</span>` : ''}
                </div>
                ${renderSearchShortcuts(a)}
            </td>
            <td class="intervento-cell">
                <div>${esc(a.data_pianificazione || '-')}</div>
                <div class="mini">${esc(a.ora_pianificazione || '')} ${a.operatore ? '· ' + esc(a.operatore) : ''}</div>
            </td>
            <td class="tel-cell" data-label="Telefono">
                <input class="tel-input edit-input" value="${esc(a.telefono || '')}" placeholder="Tel..."
                       onblur="saveInlineContact(${a.id}, 'telefono', this.value)">
            </td>
            <td class="email-cell" data-label="Email / PEC">
                <input class="email-input edit-input" value="${esc(a.email || '')}"
                       placeholder="mail@dominio.it / pec@pec.it"
                       onblur="saveInlineContact(${a.id}, 'email', this.value)">
                <div class="email-badges">
                    ${emails.length ? emails.map(e => `<span class="badge ${e.pec ? 'pec' : ''}">${e.pec ? 'PEC' : 'MAIL'}</span>`).join('') : ((a.email || '').trim() ? `<span class="badge" style="background:var(--surface-raised, #334155);color:var(--text-muted);font-size:0.65rem;">${esc(a.email)}</span>` : '')}
                </div>
            </td>
            <td class="invii-cell">
                <div class="invii-status">${inviiLabel}</div>
                <div class="risposta-row">
                    <label class="risposta-label">
                        <input type="checkbox" class="risposta-check" ${a.risposta ? 'checked' : ''}
                                onchange="saveRisposta(${a.id}, this.checked)">
                        <span>Risposta</span>
                    </label>
                    <input class="note-input" value="${esc(a.note || '')}" placeholder="Nota risposta"
                           onblur="saveRisposta(${a.id}, undefined, this.value)">
                </div>
            </td>
        </tr>
    `;
}

function onRowCheckChange(cb) {
    const id = Number(cb.dataset.id);
    if (cb.checked) {
        selectedAziendeIds.add(id);
    } else {
        selectedAziendeIds.delete(id);
    }
    const tr = cb.closest('tr');
    if (tr) tr.classList.toggle('selected', cb.checked);
    updateBulkButton();
}

async function saveInlineContact(id, field, value) {
    const row = aziende.find(a => Number(a.id) === Number(id));
    if (!row) return;
    if ((row[field] || '') === (value || '').trim()) return;
    try {
        await fetchApi(`/api/lettere/aziende/${id}/contatto`, {
            method: 'POST',
            body: JSON.stringify({ [field]: value.trim() })
        });
        row[field] = value.trim();
        renderAziende();
        showToast('Contatto salvato');
    } catch (e) {
        showToast(e.message, true);
    }
}

async function saveRisposta(id, checked, noteValue) {
    const row = aziende.find(a => Number(a.id) === Number(id));
    if (!row) return;
    const risposta = checked !== undefined ? checked : row.risposta;
    const note = noteValue !== undefined ? noteValue : row.note;
    try {
        await fetchApi(`/api/lettere/aziende/${id}/risposta`, {
            method: 'POST',
            body: JSON.stringify({ risposta: checked, note })
        });
        if (checked !== undefined) row.risposta = checked ? 1 : 0;
        if (noteValue !== undefined) row.note = noteValue;
        renderAziende();
        showToast('Risposta aggiornata');
    } catch (e) {
        showToast(e.message, true);
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
    });
    updateBulkButton();
}

function updateBulkButton() {
    const count = selectedAziende().filter(a => splitEmails(a.email).length).length;
    const bulkBtn = document.getElementById('bulkOpenBtn');
    if (bulkBtn) bulkBtn.disabled = count === 0;
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
                    azienda_ids: item.azienda_ids || [item.azienda_id],
                    contatori: item.contatori,
                    email: item.email,
                    template_id: templateId,
                    subject_override: currentSubject,
                    body_override: currentBody,
                    allegati: item.allegati,
                })
            });
            const data = await res.json();
            if (data.ok) {
                ok++;
                const countText = item.contatori && item.contatori.length > 1 ? ` (${item.contatori.length} contatori)` : '';
                log.innerHTML += `<div class="log-ok">OK ${esc(item.email)}${countText}</div>`;
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
