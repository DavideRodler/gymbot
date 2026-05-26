# 🏋️ Gym Bot — Sport Polimi

Bot Telegram che prenota **da solo** il tuo slot in palestra ogni notte a
mezzanotte (quando si aprono le prenotazioni per 2 giorni dopo). Tu fai il login
SPID una volta ogni tanto e gli passi i "cookie"; al resto pensa lui.

> ⚠️ **Stato del progetto:** la parte di login e di lettura calendario del sito
> Sportrick è stata analizzata (vedi `FINDINGS.md`). Restano da confermare **due
> dettagli tecnici** (l'indirizzo esatto che elenca gli slot e quello che conferma
> la prenotazione): si ottengono con una semplice "registrazione" del browser
> durante una prenotazione vera (istruzioni in `FINDINGS.md`, sezione 7). Finché
> non sono inseriti, i comandi funzionano ma `/test` e la prenotazione potrebbero
> non trovare gli slot.

---

## Cosa fa, in breve

- Ogni ora controlla la tua sessione e ti avvisa solo se è scaduta/assente.
- Ogni sera fa anche un controllo alle **23:59**, appena prima di prenotare.
- Alle **23:59:45** carica in anticipo gli slot del giorno bersaglio.
- Alle **00:00:00** spara la prenotazione (è una gara con gli altri utenti!).
- Ti manda una notifica col risultato.

---

## 1. Creare il bot su Telegram (BotFather)

1. Su Telegram cerca **@BotFather** e aprilo.
2. Scrivi `/newbot`, segui le istruzioni (nome + username che finisce per `bot`).
3. BotFather ti dà un **TOKEN** lungo tipo `123456:AAH...`. **Copialo**, ti serve.
4. Apri la chat col TUO nuovo bot e scrivi `/start` (così la chat esiste).

### Trovare il tuo TELEGRAM_CHAT_ID
- Apri questo indirizzo nel browser, mettendo il tuo token:
  `https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates`
- Scrivi un messaggio qualsiasi al bot, poi ricarica la pagina.
- Cerca `"chat":{"id":123456789` → quel numero è il tuo **CHAT_ID**.

---

## 2. Fare il fork del repository su GitHub

1. Vai sulla pagina del repo su GitHub.
2. In alto a destra clicca **Fork** → **Create fork**. Ora il progetto è tuo.

---

## 3. Aggiungere i Secrets su GitHub

Nel TUO fork: **Settings → Secrets and variables → Actions → New repository secret**.
Aggiungi questi:

| Nome secret | Valore | Obbligatorio |
|-------------|--------|--------------|
| `TELEGRAM_BOT_TOKEN` | il token di BotFather | ✅ |
| `TELEGRAM_CHAT_ID` | il tuo chat id | ✅ |
| `SLOT_TIME` | es. `08:30` | ✅ |
| `SESSION_COOKIES` | lascialo vuoto per ora (lo manderai col bot) | ✅ (anche vuoto) |
| `GH_PAT` | token personale per salvare i cookie aggiornati | facoltativo |

> **`GH_PAT` (facoltativo ma comodo):** se lo metti, quando rinnovi i cookie col
> comando `/cookies` il bot li **risalva nei Secrets** così sopravvivono ai
> riavvii. Crealo in **GitHub → Settings → Developer settings → Personal access
> tokens → Fine-grained**, dando al token il permesso **Secrets: Read and write**
> sul tuo fork. Senza `GH_PAT`, dopo ogni riavvio dovrai rifare `/cookies`.

Poi **abilita le Actions**: scheda **Actions** del fork → conferma di volerle
attivare. Il bot parte al primo `push` o puoi avviarlo a mano da
**Actions → gym-bot → Run workflow**.

---

## 4. Estrarre i cookie (DevTools → Copy as cURL)

I cookie di login sono *HttpOnly*: nessun bottone/script può leggerli, quindi si
copiano da DevTools. È rapido (apri `gymbot/bookmarklet.html` per le immagini).

1. Su **Chrome/Edge** vai su `ecomm.sportrick.com/sportpolimi` e fai **login SPID**.
2. Premi **F12** → scheda **Network** (Rete), poi **F5** per ricaricare.
3. Clicca una riga verso `ecomm.sportrick.com` (es. `Booking` o `sportpolimi`).
4. **Tasto destro → Copy → Copy as cURL**.
5. Vai al passo 5 qui sotto e incollalo nel bot.

> Se il bot risponde «mancano cookie importanti», avevi cliccato una richiesta a
> un'immagine/CSS: ripeti il punto 3 scegliendo una **pagina** del sito.

---

## 5. Mandare i cookie al bot

Sulla chat Telegram col bot scrivi `/cookies` + spazio e **incolla** il cURL:

```
/cookies curl 'https://ecomm.sportrick.com/sportpolimi/Booking' -H 'cookie: ...'
```

Il bot estrae da solo i cookie. Risponde **✅ Sessione salvata!** se funzionano,
altrimenti ti dice cosa manca. Accetta anche la stringa cookie grezza
(`nome=valore; ...`).

---

## 6. Scegliere lo slot

```
/slot            → mostra lo slot attuale e la griglia valida
/slot 08:30      → imposta le 08:30
```

Orari validi (ogni 90 min):
`07:00 08:30 10:00 11:30 13:00 14:30 16:00 17:30 19:00 20:30 22:00`

---

## 7. Verificare che funzioni

```
/test
```

Fa una prova **senza prenotare**: controlla la sessione, cerca gli slot del
giorno bersaglio e ti dice se trova il tuo orario. Usa anche `/status` per vedere
sessione, prossima prenotazione e ultimo risultato.

---

## 8. Quando arriva «Sessione scaduta»

Vuol dire che il login SPID è scaduto (succede ogni tot giorni). Soluzione:
1. Rifai **login SPID** sul sito.
2. DevTools → Network → **Copy as cURL** (passo 4).
3. Manda di nuovo `/cookies ...` al bot.

---

## 9. Condividere il bot con un'altra persona

Ognuno deve avere **il proprio** bot e i propri secret (è single-user):
1. L'altra persona fa il **fork** del repo.
2. Crea il **suo** bot con BotFather (token e chat id suoi).
3. Mette i **suoi** Secrets.
4. Estrae i **suoi** cookie col bookmarklet.

Così ogni persona prenota col proprio account, senza interferenze.

---

## Comandi del bot

| Comando | Cosa fa |
|---------|---------|
| `/start` | stato e configurazione |
| `/setup` | istruzioni per i cookie |
| `/cookies <json>` | salva e valida i cookie |
| `/slot [orario]` | mostra/cambia lo slot |
| `/status` | sessione, prossima e ultima prenotazione |
| `/test` | prova senza prenotare |
| `/stop` | metti in pausa le prenotazioni |
| `/resume` | riattiva le prenotazioni |
| `/help` | elenco comandi |

Prenoto **solo da Lunedì a Venerdì**: se il giorno bersaglio (oggi+2) cade di
sabato o domenica, salto in silenzio. Lo stato attivo/pausa è ricordato nel
secret `BOOKING_ACTIVE` (se hai impostato `GH_PAT`); altrimenti vale fino al
prossimo riavvio.

---

## Note tecniche

- Stack: Python 3.11, python-telegram-bot 21, APScheduler, requests, PyNaCl.
- Fuso orario: `Europe/Rome` (la mezzanotte è quella italiana).
- Nessun database: la configurazione vive nei Secrets / variabili d'ambiente
  (e in un `config.json` locale, ignorato da git).
- Lo scheduler GitHub Actions (cron) può avere ritardi di qualche minuto sotto
  carico: è il limite noto delle Actions, non del bot.
- Dettagli del reverse engineering del sito: vedi `FINDINGS.md`.
