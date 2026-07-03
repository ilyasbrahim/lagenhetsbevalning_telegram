# Housing Alerts

Ett enkelt Python-system som bevakar offentliga bostadsannonser utan inloggning och skickar Telegram-notiser när nya annonser hittas.

Systemet ansöker inte om lägenheter, kringgår inte BankID/captcha/köregler och läser bara publika annonssidor.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Telegram

1. Skapa en bot via `@BotFather` i Telegram och kopiera bot-token.
2. Skicka ett meddelande till boten från den chatt som ska få notiser.
3. Hämta `chat_id` genom att öppna:

```text
https://api.telegram.org/botDIN_TOKEN/getUpdates
```

4. Skapa `.env` från exemplet:

```bash
cp .env.example .env
```

5. Fyll i:

```text
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Om `.env` saknas kör systemet ändå, men Telegram-notiser hoppas över.

## Kör manuellt

```bash
python main.py
```

Första gången en annons hittas sparas den i `data/listings.db` och notifieras. Vid senare körningar uppdateras `last_seen_at`, men dubletter skickas inte igen.

För felsökning utan sidoeffekter:

```bash
python main.py --dry-run
```

Dry-run hämtar annonser, utvärderar filter och databasstatus, men skriver inte till databasen och skickar inte Telegram.

För att rensa databasen vid test:

```bash
python main.py --reset-db
```

## Konfiguration

Alla bevakningar ligger i `config.json`.

```json
{
  "name": "Wallenstam - Stockholm",
  "url": "https://www.wallenstam.se/sv/bostader/lediga-bostader/?Status=Available&Region=Stockholm",
  "enabled": true,
  "scraper": "wallenstam",
  "mode": "url_only",
  "filters": {
    "areas": ["Stockholm"],
    "max_rent": 12000,
    "min_rooms": 1
  }
}
```

Stödda lägen:

- `url_only`: filtren finns i URL:en, men annonser filtreras ändå lokalt i Python.
- `browser_filter`: för sidor där filter normalt kräver Playwright. Första versionen föredrar lokal filtrering om möjligt.
- `browser_or_api`: scraper försöker hitta publika JSON-anrop och faller annars tillbaka till Playwright.

Lokal filtrering:

- `max_rent`: filtrerar bort annonser med högre hyra.
- `min_rooms`: filtrerar bort annonser med färre rum.
- `areas`: matchar mot område, adress och titel.

Om ett fält saknas kraschar inte systemet. Med `allow_missing_rent` och `allow_missing_rooms` i `settings` kan annonser med saknad hyra eller rum skickas ändå.

## Scrapers

Scrapers finns i `scrapers/` och ska returnera annonser i detta format:

```python
{
    "title": "...",
    "district": "...",
    "area": "...",
    "rent": "...",
    "rooms": "...",
    "size": "...",
    "address": "...",
    "url": "...",
    "external_id": "...",
    "raw_data": {}
}
```

För att lägga till en ny sida:

1. Skapa `scrapers/ny_sida.py`.
2. Ärva från `BaseScraper`.
3. Implementera `fetch_listings()`.
4. Registrera scraperklassen i `scrapers/__init__.py`.
5. Lägg till en check i `config.json`.

## Wallenstam

Wallenstam-scrapern använder först `requests` och BeautifulSoup. Om inga annonser finns i statisk HTML provar den Playwright. URL-filter används som startpunkt, men annonserna filtreras alltid lokalt enligt `config.json`.

## Heimstaden

Heimstaden ändrar inte URL när man filtrerar. Scrapern bygger därför inte på filtrerad URL. Den försöker först hitta publika JSON/API-resultat och faller sedan tillbaka till Playwright. Om filterklick är ostabilt är avsikten att hämta alla publika annonser och filtrera lokalt.

## Schemaläggning

Kör försiktigt, till exempel var 30:e minut.

Cron-exempel:

```cron
*/30 * * * * cd "/Users/ilyas/Downloads/lägenhetsbevakning telegram" && /usr/bin/env bash -lc 'source .venv/bin/activate && python main.py' >> housing-alerts.log 2>&1
```

På macOS kan du även använda `launchd`. Skapa en plist som kör samma kommando med `StartInterval` satt till `1800`.

## GitHub Actions

Projektet innehåller ett GitHub Actions-workflow i `.github/workflows/housing-alerts.yml`. Det kör `python main.py` i molnet när workflowet triggas med `workflow_dispatch`.

GitHubs interna `schedule` används inte, eftersom den kan vara opålitlig i vissa repos. Använd i stället en extern cron-tjänst som timer och låt den trigga workflowet via GitHub API.

Så här sätter du upp det:

1. Skapa ett privat GitHub-repo.
2. Pusha projektet till repot, inklusive `data/listings.db`.
3. Gå till GitHub-repot och öppna `Settings` -> `Secrets and variables` -> `Actions`.
4. Lägg till repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Gå till fliken `Actions` och aktivera workflows om GitHub ber om det.
6. Öppna workflowet `Housing Alerts` och kör det manuellt med `Run workflow` första gången.
7. Läs loggarna under workflow-körningen om något inte skickas.

Workflowet gör detta:

- Checkar ut repot.
- Installerar Python och `requirements.txt`.
- Installerar Playwright Chromium.
- Skapar `.env` från GitHub Secrets.
- Kör `python main.py`.
- Commits tillbaka `data/listings.db` om databasen ändrats.

Databasen måste versioneras i repot för att undvika dubbletter mellan körningar. Om samma annonser skickas igen, kontrollera:

- Att `data/listings.db` finns i repot.
- Att workflowet har `permissions: contents: write`.
- Att `Settings` -> `Actions` -> `General` tillåter write permissions för workflowet.
- Att commit-steget inte misslyckas i Actions-loggen.
- Att du inte har kört `python main.py --reset-db` utan att förstå att historiken nollställs.

Om Telegram inte skickar:

- Kontrollera att `TELEGRAM_BOT_TOKEN` och `TELEGRAM_CHAT_ID` finns som GitHub Secrets.
- Kontrollera att boten har fått ett meddelande från chatten minst en gång.
- Kör workflowet manuellt och läs loggen för `telegram_failed`.
- Testa lokalt med samma värden i `.env`.

Tänk på att offentliga bostadssidor kan blockera datacentertrafik. Om en sida fungerar lokalt men inte i GitHub Actions kan det bero på GitHubs runner-nätverk.

## Extern cron via GitHub API

En extern cron-tjänst, till exempel cron-job.org, kan trigga workflowet med GitHub REST API. Då är cron-tjänsten timern, medan GitHub Actions bara kör jobbet när det får en `workflow_dispatch`.

Endpoint:

```text
POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches
```

För detta projekt kan `workflow_id` vara:

```text
housing-alerts.yml
```

Body:

```json
{
  "ref": "main"
}
```

Exempel med `curl`:

```bash
curl -L \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer GITHUB_TOKEN_HERE" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/OWNER/REPO/actions/workflows/housing-alerts.yml/dispatches \
  -d '{"ref":"main"}'
```

Skapa en GitHub Personal Access Token:

1. Skapa en fine-grained token i GitHub.
2. Ge token access endast till detta repo.
3. Ge den permission för Actions/workflows så den kan köra `workflow_dispatch`.
4. Spara token i den externa cron-tjänsten, inte i repot.

Exempelinställning i cron-job.org eller liknande:

- Method: `POST`
- URL: `https://api.github.com/repos/OWNER/REPO/actions/workflows/housing-alerts.yml/dispatches`
- Header: `Accept: application/vnd.github+json`
- Header: `Authorization: Bearer <GITHUB_PAT>`
- Header: `X-GitHub-Api-Version: 2022-11-28`
- Header: `Content-Type: application/json`
- Body: `{"ref":"main"}`

Rekommenderade intervall:

- Test: var 30:e minut.
- Produktion: 3 gånger per dag, till exempel 08:00, 14:00 och 20:00 svensk tid.

Kör inte oftare än var 30:e minut, så bostadssidorna inte belastas i onödan.

Om externa cron-körningar inte startar workflowet:

- Kontrollera att URL:en innehåller rätt `OWNER`, `REPO` och `housing-alerts.yml`.
- Kontrollera att body är exakt `{"ref":"main"}`.
- Kontrollera att token har access till repot och permission att köra Actions/workflows.
- Kontrollera att token skickas som `Authorization: Bearer <GITHUB_PAT>`.
- Gå till `Settings` -> `Branches` och kontrollera att default branch är `main`.
- Gå till `Settings` -> `Actions` -> `General` och kontrollera att Actions är tillåtna.
- Gå till `Actions` -> `Housing Alerts`. Menyn med tre punkter ska visa `Disable workflow`. Om den visar `Enable workflow` behöver workflowet aktiveras.
- Kör workflowet manuellt med `Run workflow` för att bekräfta att själva jobbet fungerar.

## Felsökning

- Kör `python main.py` och läs loggarna.
- Om Playwright saknas: kör `playwright install chromium`.
- Om Telegram inte skickar: kontrollera `.env`, bot-token och `chat_id`.
- Om en sida ger noll annonser: öppna URL:en i webbläsaren utan inloggning och kontrollera att annonser faktiskt syns publikt.
- Om HTML ändras på en sida behöver motsvarande scraper uppdateras.
