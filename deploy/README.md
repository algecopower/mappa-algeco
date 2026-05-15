# Deploy multi-tenant — Mappa Algeco

Architettura del deploy in produzione dopo la migrazione al router.

## Componenti

```
Cloudflare wildcard *.mappa-algeco.com
         │
         ▼
cloudflared-tunnel.service    (systemd, indipendente)
         │
         ▼
mappa-algeco-router.service   (systemd, supervisor)
   │
   ├─ router.py (porta 5050)              ← reverse proxy by Host header + UI admin
   ├─ server.py PROVINCIA=TORINO  :5001   ← backend Torino (spawnato dal router)
   ├─ server.py PROVINCIA=PAVIA   :5002   ← backend Pavia
   └─ server.py PROVINCIA=...     :500N   ← altre province (spawnate dinamicamente)
```

## File rilevanti in questa cartella

| File | Destinazione | Note |
|---|---|---|
| `cloudflared-config.yml.example` | `/etc/cloudflared/config.yml` | wildcard ingress su localhost:5050 |
| `mappa-algeco-router.service.example` | `/etc/systemd/system/mappa-algeco-router.service` | supervisor, dipende da cloudflared-tunnel.service |

Il file `cloudflared-tunnel.service` (in `/etc/systemd/system/`) gira indipendentemente — `cloudflared tunnel run mappa-algeco` come Type=simple, Restart=always.

## Aggiungere una nuova provincia

**Operazione "self-serve" via UI:**

1. Login come admin su `torino.mappa-algeco.com` (o qualunque backend esistente).
2. Vai su `https://admin.mappa-algeco.com/` (la sessione viene riusata grazie a `SESSION_COOKIE_DOMAIN=.mappa-algeco.com`).
3. Clicca **Aggiungi provincia**, compila il form (slug, nome, ID Google Sheet, deposito, ecc.).
4. Clicca **🔍 Verifica foglio** per confermare che il foglio è leggibile e vedere il mapping colonne dedotto.
5. Crea la provincia.
6. Sulla card, clicca **📥 Inizializza DB** (primo pull completo dal foglio, ~30-60s).
7. Clicca **▶ Avvia** per spawnare il backend.

**Operazione manuale che resta:** aggiungere il record DNS su Cloudflare per il nuovo sottodominio.

- Vai su Cloudflare dashboard → mappa-algeco.com → DNS.
- Aggiungi un CNAME: `{slug}.mappa-algeco.com` → `<TUNNEL_UUID>.cfargotunnel.com` (proxied).
- Oppure (raccomandato): aggiungi una volta sola il wildcard `*.mappa-algeco.com` → `<TUNNEL_UUID>.cfargotunnel.com`, così le nuove province non richiedono più nuovi record DNS.

## Comandi utili

```bash
# Stato servizi
systemctl status mappa-algeco-router cloudflared-tunnel

# Logs
journalctl -u mappa-algeco-router -f
journalctl -u cloudflared-tunnel -f

# Restart router (i backend ripartono in cascata)
systemctl restart mappa-algeco-router

# Health interna router
curl -s http://127.0.0.1:5050/_router/health | jq

# Smoke test proxy
curl -s -H "Host: torino.mappa-algeco.com" http://127.0.0.1:5050/login | head -3
```

## Servizi systemd attivi (post-migrazione)

Solo due unit sono attive in produzione:
- `mappa-algeco-router.service` — supervisor router + backend per provincia
- `cloudflared-tunnel.service` — tunnel Cloudflare

```bash
systemctl status mappa-algeco-router cloudflared-tunnel
systemctl restart mappa-algeco-router    # comando "normale" di restart
```

⚠️ **NON usare** `systemctl restart mappa` né `systemctl restart mappa-algeco`:
quei due unit file esistono ancora sotto `/etc/systemd/system/` ma sono
**rinominati con suffisso `.disabled`** così systemd non li vede:
- `mappa.service.disabled` (vecchio launcher monolitico)
- `mappa-algeco.service.disabled` (variante mai usata)

I file restano come reference per eventuale rollback (vedi sezione sotto).

## Rollback (se qualcosa va storto)

```bash
# 1. Stop nuovo stack
systemctl stop mappa-algeco-router cloudflared-tunnel
systemctl disable mappa-algeco-router cloudflared-tunnel

# 2. Ripristina cloudflared config legacy (path-per-host)
cp /etc/cloudflared/config.yml.pre-router-bak /etc/cloudflared/config.yml

# 3. Riattiva il vecchio unit
mv /etc/systemd/system/mappa.service.disabled /etc/systemd/system/mappa.service
systemctl daemon-reload
systemctl enable mappa.service
systemctl start mappa.service
```

## Storia

- Step 1: `data/provinces.json` come fonte di verità della config province (commit `8c711df`)
- Step 2: `router.py` base con proxy by Host + ProcessManager (commit `0809e3c`)
- Step 3: UI admin nel router (commit `b233de6`)
- Step 4: probe foglio + bootstrap DB (commit `d3dc33f`)
- Step 5: cutover sistemico — cloudflared wildcard + systemd unit (questo deploy)
