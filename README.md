# Vietnam’s economy

A Streamlit dashboard for eight economic indicators. The date slider filters the charts from January 2015 onward. Each card shows its latest loaded observation, the publisher release or update date when available, and separate refresh timestamps.

## Update your existing app

1. Replace `app.py`, `requirements.txt`, and `README.md` at the root of the GitHub branch connected to Streamlit Community Cloud.
2. Keep `app.py` as the entry point and use Python 3.12. For a new deployment, select the repository and branch in **Create app**, enter `app.py`, and choose Python 3.12 in **Advanced settings**.
3. Open the updated app and select **Refresh now**. If you already run the external scheduler, update its checkout and reinstall the requirements there too. The app reads version 1 snapshots, and the next scheduler run writes version 2 with the exchange rate. You can keep your existing `SNAPSHOT_URL`.

## When a publisher connection fails

GDP, core CPI, unemployment, merchandise trade, and the refinancing-rate workbook share `nsdp.nso.gov.vn`. One connectivity problem from Community Cloud can affect all five cards. The original error alone does not identify whether the cause was DNS, TLS, an upstream failure, or a temporary connection problem.

The app retries transient failures and now reports the hostname and failure category. It keeps previously validated observations and, on a cold start, can display verified observations bundled in `app.py`. A failed check leaves the last success timestamp unchanged.

If Cloud keeps failing, run the command below from your computer or scheduler host. A successful run there confirms that the publishers are reachable from that machine. Configure the HTTPS snapshot if you want Cloud to use data fetched by that machine.

```bash
python app.py --refresh /tmp/vietnam-economy.json
```

## Run locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Keep virtual environments, snapshots, logs, and credentials outside Git.

## Refresh settings

Set environment variables in your local shell or use top-level TOML settings in the Community Cloud app’s **Secrets** settings. Substitute the real hostname. Omit `SNAPSHOT_URL` for direct publisher retrieval.

```toml
REFRESH_SECONDS = "21600"
SNAPSHOT_URL = "https://economy-data.example.com/snapshot.json"
```

- `REFRESH_SECONDS` is the active-session interval in seconds. The default is 21600, or six hours. The minimum is 10.
- A **cache TTL** defines how long cached data is fresh. Elapsed time alone performs no retrieval. The app uses shared Streamlit process state and an elapsed-time check.
- The **active-session timer** checks whether refresh is due. It reads a fresh scheduled snapshot when configured, or checks publishers directly when needed. **Refresh now** bypasses the interval and checks publishers directly.
- The **unattended schedule** runs independently of Streamlit viewers. It saves a JSON snapshot and appends publisher results to a log. A partial failure preserves the valid data and returns a nonzero exit status.
- On a Streamlit restart, the app can recover data from the scheduler snapshot. Without a snapshot, it starts with the bundled observations and fetches again. Any additional bond closes previously held only in memory are lost. A manual UI check does not write to the scheduler.
- The displayed observation, publication, and refresh timestamps are distinct. No other series is converted using the exchange rate.

## Enable unattended refresh

Use an always-on Ubuntu 24.04 host with a persistent disk. Point a dedicated DNS hostname at the server, allow inbound TCP 80 and 443, and allow outbound HTTPS. Replace the example repository and hostname. The cron example assumes the Linux account is `ubuntu`.

1. Install the services and create the scheduler environment.

   ```bash
   sudo apt-get update
   sudo apt-get install -y caddy cron git python3.12 python3.12-venv
   git clone https://github.com/YOUR-ACCOUNT/YOUR-REPO.git "$HOME/vietnam-economy"
   python3.12 -m venv "$HOME/vietnam-economy-venv"
   "$HOME/vietnam-economy-venv/bin/pip" install -r "$HOME/vietnam-economy/requirements.txt"
   sudo install -d -m 0755 -o "$(id -un)" /var/www/vietnam-economy
   ```

2. Create the initial snapshot and inspect the log.

   ```bash
   "$HOME/vietnam-economy-venv/bin/python" "$HOME/vietnam-economy/app.py" \
     --refresh /var/www/vietnam-economy/snapshot.json \
     >> "$HOME/vietnam-economy-refresh.log" 2>&1
   tail -n 8 "$HOME/vietnam-economy-refresh.log"
   ```

3. On the dedicated host, configure `/etc/caddy/Caddyfile` with your hostname.

   ```caddyfile
   economy-data.example.com {
       handle /snapshot.json {
           root * /var/www/vietnam-economy
           header Cache-Control "no-store"
           file_server
       }
       respond 404
   }
   ```

   ```bash
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl enable --now caddy cron
   sudo systemctl reload caddy
   curl -fsS https://economy-data.example.com/snapshot.json | python3.12 -m json.tool
   ```

4. Run `crontab -e` as `ubuntu` and add this line. It runs at minute 11 every six hours in the host’s timezone. Change the `/home/ubuntu` paths if needed. Use the same lock for manual scheduler-host runs after enabling cron.

   ```cron
   11 */6 * * * /usr/bin/flock -n /home/ubuntu/vietnam-economy-refresh.lock /home/ubuntu/vietnam-economy-venv/bin/python /home/ubuntu/vietnam-economy/app.py --refresh /var/www/vietnam-economy/snapshot.json >> /home/ubuntu/vietnam-economy-refresh.log 2>&1
   ```

5. Set `SNAPSHOT_URL` in Community Cloud to the verified HTTPS endpoint. Close the dashboard and wait for the next scheduled time. Check for new log entries in `~/vietnam-economy-refresh.log` and matching attempt timestamps in the HTTPS snapshot. Reopen the dashboard and compare the card timestamps with the snapshot.

Snapshots contain public economic observations. The HTTPS endpoint serves only `snapshot.json`. Logs stay on the scheduler host. Keep the cron expression and `REFRESH_SECONDS` in sync if you change the cadence.

## Indicator definitions and verification

| Indicator | Publisher and observed value | Dashboard value and coverage |
| --- | --- | --- |
| Real GDP growth. Quarterly `NGDP_R_PA_XDC`. Growth is `100 × (current real GDP / real GDP four quarters earlier − 1)`, using the two individual quarters from the same constant-price sheet. NSO can revise the estimates. | [NSO quarterly report](https://www.nso.gov.vn/tin-tuc-thong-ke/2026/10/bao-cao-tinh-hinh-kinh-te-xa-hoi-quy-iii-va-9-thang-nam-2026/), 2026 Q3, estimated **9.95% YoY**. | **9.948% YoY** from 3,004,778.442597 and 2,732,908.972376 billion VND at 2020 prices, rounding to 9.95%. Loaded 2015 Q1–2026 Q3. [Workbook](https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/GDP_VNM.xlsx) |
| Core CPI. Monthly official YoY rate excluding staple and fresh foods, energy, and administered items including health and education services. The current workbook changed CPI bases in November 2025. | [NSO March 2019 release](https://www.nso.gov.vn/en/data-and-statistics/2019/11/cpi-gold-and-usd-price-indexes-march-2019/), **1.84% YoY**. [NSO October 2026 release](https://www.nso.gov.vn/wp-content/uploads/2026/11/CPI-chi-so-gia-vang-va-chi-so-gia-do-la-My-thang-Muoi-va-10-thang-nam-2026.pdf), **4.04% YoY**. | **1.840%** and **4.036%** respectively. The historical workbook supplies April 2015–February 2019, monthly releases bridge March 2019–July 2020, and the current workbook begins in August 2020. [Historical workbook](https://nsdp.nso.gov.vn/GSO-chung/tieng%20anh/excel/gso/TKG.%20CPI_Vietnam.xlsx), [current workbook](https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/CPI_VNM.xlsx) |
| Refinancing rate. Monthly `FIR_PA` archive and nine verified SBV effective-date changes in 2019–2023. | [Government Portal reporting SBV Decision 1123](https://en.baochinhphu.vn/central-bank-cuts-regulatory-interest-rate-111230616172123401.htm), effective June 19, 2023, **4.5% per year**. | **4.500% per year** on the same date. The live check rechecks the archive. A reliable current-rate feed remains unavailable. The chart ends at the last verified decision. [Archive](https://nsdp.nso.gov.vn/GSO-chung/tieng%20anh/excel/sbv/INR_Vietnam%202019.xlsx) |
| 10-year bond yield. ADB Market Watch VND secondary-market closing yield to maturity. HNX transaction data were unreachable from the test host, and a suitable historical benchmark download was unverified. | [ADB Market Watch](https://asianbondsonline.adb.org/vietnam/), November 18, 2026 close, **4.462%**. [ADB curve](https://asianbondsonline.adb.org/charts/government_bond_yields.php?economies=VN), **4.040%** end-2025 and **4.465%** end-October 2026 at verification. Both pages roll forward. | Same values, read directly. Two reference points and one dated close start the chart. A persistent snapshot accumulates newly captured closes. [Methodology](https://asianbondsonline.adb.org/market_watch_specs.php?market=country#govt_bonds) |
| Coffee. Monthly nominal USD/kg, World Bank Pink Sheet’s ICO Robustas indicator for New York and Le Havre/Marseilles ex-dock markets. A refreshable Vietnam farm-gate history was unverified. | [World Bank monthly workbook](https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx), October 2026, **USD 3.63/kg**. | **USD 3.630/kg**, directly. The [ICO report](https://ico.org/documents/cy2026-27/cmr-1026-e.pdf) gives 164.19 US cents/lb, about USD 3.620/kg, for the same month. The app consistently uses the World Bank vintage. |
| Merchandise trade. Monthly exports FOB `TXG_FOB_USD` minus imports CIF `TMG_CIF_USD`, using the same unadjusted NSO table. Positive means surplus. The workbook flags data from January 2018 as preliminary. | [NSO October report](https://www.nso.gov.vn/en/data-and-statistics/2026/11/report-socio-economic-performance-in-october-and-10-months-of-2026/), October 2026, preliminary **USD 7.31 billion surplus**. | **USD 7.307 billion**, from `(64,670.426411 − 57,363.689155) / 1,000`. Rounds to USD 7.31 billion. Missing paired months stay blank. |
| Unemployment. Quarterly NSO `LUR_PT`, national working-age labor force. Lower age threshold is 15. Upper limits follow the applicable working-age definition. In 2026 normal statutory retirement ages were 61 years 6 months for men and 57 years for women. | [NSO quarterly press release](https://www.nso.gov.vn/du-lieu-va-so-lieu-thong-ke/2026/10/thong-cao-bao-chi-tinh-hinh-kinh-te-xa-hoi-quy-iii-va-9-thang-nam-2026/), 2026 Q3, **2.22%**. | **2.218%**, rounding to 2.22%. Preliminary per the workbook. [Retirement-age schedule](https://baochinhphu.vn/quy-dinh-moi-ve-tuoi-nghi-huu-102283105.htm) |
| Exchange rate. BIS `M.VN.VND.E`, monthly end-of-period. VND per USD is the number of dong per one US dollar. A rise means a weaker dong. | [BIS series](https://data.bis.org/topics/XRU/BIS%2CWS_XRU%2C1.0/M.VN.VND.E), August 2026, **25,610 VND per USD**. | **25,610 VND per USD**, directly from the [CSV API](https://stats.bis.org/api/v1/data/BIS,WS_XRU,1.0/M.VN.VND.E). Verified coverage January 2015–August 2026. |

Examples of the core CPI bridge are the [April 2019 report](https://www.nso.gov.vn/en/data-and-statistics/2019/10/report-social-and-economic-situation-four-months-and-in-april-2019/), [October 2019 table](https://www.nso.gov.vn/wp-content/uploads/2019/11/CPI-10-2019-E01.pdf), [May 2020 report](https://www.nso.gov.vn/en/data-and-statistics/2020/07/socio-economic-situation-in-the-first-5-months-of-2020/) and [July 2020 release](https://www.nso.gov.vn/en/data-and-statistics/2020/07/consumer-price-index-gold-and-usd-price-indexes-july-2020/). These are individual monthly YoY observations.

Sources for the additional refinancing-rate steps are the government announcements for [September 2019](https://en.baochinhphu.vn/central-bank-slashes-interest-rates-applicable-since-sep-16-11136132.htm), [March 2020](https://baochinhphu.vn/giam-hang-loat-lai-suat-dieu-hanh-tu-ngay-17-3-102269631.htm), [May 2020](https://en.baochinhphu.vn/central-bank-lower-interest-rates-11138191.htm), [October 2020](https://en.baochinhphu.vn/central-bank-slashes-benchmark-rates-in-favor-of-economic-recovery-11139411.htm), [September 2022](https://baochinhphu.vn/ngan-hang-nha-nuoc-dieu-chinh-mot-so-muc-lai-suat-tu-ngay-23-9-102220922175228897.htm), [October 2022](https://baochinhphu.vn/ngan-hang-nha-nuoc-viet-nam-dieu-chinh-cac-muc-lai-suat-dieu-hanh-102221024210117723.htm), [April 2023](https://xaydungchinhsach.chinhphu.vn/ngan-hang-nha-nuoc-tiep-tuc-giam-lai-suat-119230401064608221.htm), [May 2023](https://en.baochinhphu.vn/sbv-continues-cutting-regulatory-interest-rates-111230524155951848.htm), and [June 2023](https://en.baochinhphu.vn/central-bank-cuts-regulatory-interest-rate-111230616172123401.htm).

## Execution evidence

The updated workspace build ran against live publishers under Python 3.12.14. The excerpt is from that run. Counts include retained archival observations. Timestamps are UTC, corresponding to the evening of November 19, 2026 in U.S. Central time. The chat version expresses the bundled history as readable tables, rounded where needed. Live loaders use the precision provided by the sources.

```text
2026-11-20T01:58:45+00:00 gdp [NSO] OK observations=47 latest=2026-Q3
2026-11-20T01:58:44+00:00 core [NSO] OK observations=139 latest=2026-10
2026-11-20T01:58:43+00:00 policy [SBV archive + Government Portal] OK limited archive observations=62 latest=2023-06-19
2026-11-20T01:58:37+00:00 bond [ADB AsianBondsOnline] OK observations=3 latest=2026-11-18
2026-11-20T01:58:38+00:00 coffee [World Bank / ICO] OK observations=142 latest=2026-10
2026-11-20T01:58:45+00:00 trade [NSO] OK observations=118 latest=2026-10
2026-11-20T01:59:34+00:00 jobs [NSO] OK observations=43 latest=2026-Q3
2026-11-20T01:58:44+00:00 fx [BIS] OK observations=140 latest=2026-08
```

A separate Streamlit AppTest on that build simulated a failure at every publisher. All eight charts rendered from bundled data on initial load and after a manual retry. Version 1 snapshot loading was also tested. Community Cloud retrieval and unattended cron execution remain unverified.
