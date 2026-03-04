# 🌐 Guida Cloudflare Tunnel

Configura accesso remoto permanente con URL fissi.

## 1. Acquista il dominio

### Opzione A: Direttamente su Cloudflare (consigliato)
1. Vai su https://dash.cloudflare.com
2. Crea account (gratuito)
3. Vai su "Domain Registration" → "Register Domains"
4. Cerca `mappa-algeco.com` (o altro nome)
5. Acquista (~€10/anno per .com)

### Opzione B: Dominio esistente
Se hai già un dominio altrove, aggiungilo a Cloudflare:
1. "Add a Site" su Cloudflare
2. Segui le istruzioni per cambiare i nameserver

## 2. Installa Cloudflared

### Windows
Scarica il .msi da:
https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

Oppure con winget:
```powershell
winget install Cloudflare.cloudflared
```

### Verifica installazione
```powershell
cloudflared --version
```

## 3. Autenticazione

Collega cloudflared al tuo account Cloudflare:

```powershell
cloudflared tunnel login
```

Si aprirà il browser per autorizzare. Seleziona il dominio.

## 4. Crea il Tunnel

```powershell
cloudflared tunnel create mappa-algeco
```

Verrà creato un file di credenziali. Annotati l'UUID del tunnel mostrato.

## 5. Configura i DNS

Per ogni provincia, crea un record DNS che punta al tunnel:

```powershell
# Per Torino
cloudflared tunnel route dns mappa-algeco torino.mappa-algeco.com

# Per Pavia  
cloudflared tunnel route dns mappa-algeco pavia.mappa-algeco.com
```

## 6. Crea il file di configurazione

Crea il file `~/.cloudflared/config.yml` (o `%USERPROFILE%\.cloudflared\config.yml` su Windows):

```yaml
tunnel: mappa-algeco
credentials-file: C:\Users\TUO_UTENTE\.cloudflared\XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX.json

ingress:
  # Torino
  - hostname: torino.mappa-algeco.com
    service: http://localhost:5001
  
  # Pavia
  - hostname: pavia.mappa-algeco.com
    service: http://localhost:5002
  
  # Catch-all (obbligatorio)
  - service: http_status:404
```

Sostituisci:
- `TUO_UTENTE` con il tuo nome utente Windows
- `XXXXXXXX-...` con l'UUID del tunnel (lo trovi nella cartella .cloudflared)

## 7. Testa il tunnel

```powershell
cloudflared tunnel run mappa-algeco
```

Se funziona, vedrai log di connessione. Premi Ctrl+C per fermare.

## 8. Usa lo script

Modifica `server_cloudflare.py` con il tuo dominio:

```python
TUNNEL_NAME = "mappa-algeco"
DOMINIO_BASE = "mappa-algeco.com"  # Il tuo dominio
```

Poi avvia:
```powershell
python server_cloudflare.py
```

## Risultato finale

I tuoi colleghi potranno accedere da qualsiasi rete a:

- 📍 **Torino**: https://torino.mappa-algeco.com/mappa_torino.html
- 📍 **Pavia**: https://pavia.mappa-algeco.com/mappa_pavia.html
- 📋 **Distinte Torino**: https://torino.mappa-algeco.com/distinte
- 📋 **Distinte Pavia**: https://pavia.mappa-algeco.com/distinte

## Tunnel come servizio Windows (opzionale)

Per avviare il tunnel automaticamente all'avvio del PC:

```powershell
cloudflared service install
```

Questo installa cloudflared come servizio Windows.

## Troubleshooting

### "tunnel not found"
Verifica il nome del tunnel:
```powershell
cloudflared tunnel list
```

### "DNS record not found"
Verifica i record DNS su Cloudflare Dashboard → DNS.

### Connessione lenta
Cloudflare usa il datacenter più vicino. In Italia dovrebbe essere Milano/Roma.

### Errore certificato SSL
Attendi qualche minuto, Cloudflare genera il certificato automaticamente.

## Costi

- **Cloudflare Tunnel**: GRATUITO
- **Dominio .com**: ~€10-15/anno
- **SSL/HTTPS**: Incluso gratuito
- **Protezione DDoS**: Inclusa gratuita
