# Vietnam’s economy

Eight economic indicators at their published frequencies. The date slider filters the charts from January 2015 onward. Each card’s latest observation and refresh status refer to the full loaded series. Months, quarters, and survey years use period-end dates for plotting. Decisions and market closes use their actual dates.

## Run locally

From the directory containing the three repository files, use Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Keep `.venv`, snapshots, and logs outside Git. To run the same publisher retrieval used by the scheduler, with a writable directory of your choice:

```bash
python app.py --refresh /tmp/vietnam-economy.json
```

## Deploy on Streamlit Community Cloud

1. Create a GitHub repository and commit `app.py`, `requirements.txt`, and `README.md` at its root.
2. In Streamlit Community Cloud, choose **Create app** and select the repository and branch. Set the main file path to `app.py`.
3. Under **Advanced settings**, select **Python 3.12**. To enable unattended retrieval, complete the scheduler setup below and enter its `SNAPSHOT_URL` in the app’s **Secrets** settings. You can deploy with publisher retrieval first and add the snapshot URL later.
4. Select **Deploy**. Open the dashboard, confirm that the cards load, and use **Refresh now** to check publisher access from Community Cloud.

| Setting | Default | Purpose |
| --- | --- | --- |
| `REFRESH_SECONDS` | `21600` | Integer number of seconds between active-session checks. Minimum 10. |
| `SNAPSHOT_URL` | Unset | HTTPS URL for the scheduler’s JSON snapshot. |

Use shell environment variables locally and top-level TOML settings in Community Cloud. Replace the example hostname with the one you configure for the scheduler.

```toml
REFRESH_SECONDS = "21600"
SNAPSHOT_URL = "https://economy-data.example.com/snapshot.json"
```

The data sources and the snapshot in this setup are public and require no API key. Keep server access credentials in your hosting account or environment and outside the repository.

## Schedule unattended retrieval

Use a separate, always-on Ubuntu 24.04 host with a persistent disk, outbound HTTPS access, and a public DNS name. Point the hostname at the server and allow inbound TCP ports 80 and 443 for Caddy. The example assumes a login named `ubuntu` and a dedicated hostname. Replace the Git URL and hostname, and adjust `/home/ubuntu` in the cron entry if your account differs.

1. Sign in to the host as `ubuntu`. Install the services, clone the repository, and create the Python environment.

   ```bash
   sudo apt-get update
   sudo apt-get install -y caddy cron git python3.12 python3.12-venv
   git clone https://github.com/YOUR-ACCOUNT/YOUR-REPO.git "$HOME/vietnam-economy"
   python3.12 -m venv "$HOME/vietnam-economy-venv"
   "$HOME/vietnam-economy-venv/bin/pip" install -r "$HOME/vietnam-economy/requirements.txt"
   sudo install -d -m 0755 -o "$(id -un)" /var/www/vietnam-economy
   ```

2. Generate the initial snapshot. The log stays in the scheduler user’s home directory. The snapshot is stored outside the checkout and survives ordinary restarts of the scheduler host.

   ```bash
   "$HOME/vietnam-economy-venv/bin/python" "$HOME/vietnam-economy/app.py" \
     --refresh /var/www/vietnam-economy/snapshot.json \
     >> "$HOME/vietnam-economy-refresh.log" 2>&1
   tail -n 8 "$HOME/vietnam-economy-refresh.log"
   ```

3. On the dedicated host, set `/etc/caddy/Caddyfile` to the following configuration with your real hostname. Only the snapshot path is served.

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

4. Run `crontab -e` as `ubuntu` and add this line. The schedule runs at minute 11 every six hours in the host’s timezone. `flock` prevents overlapping scheduled writers. Keep manual invocations on the scheduler host under the same lock after enabling cron.

   ```cron
   11 */6 * * * /usr/bin/flock -n /home/ubuntu/vietnam-economy-refresh.lock /home/ubuntu/vietnam-economy-venv/bin/python /home/ubuntu/vietnam-economy/app.py --refresh /var/www/vietnam-economy/snapshot.json >> /home/ubuntu/vietnam-economy-refresh.log 2>&1
   ```

5. Set `SNAPSHOT_URL` in Community Cloud to the HTTPS URL tested in step 3. To verify unattended execution, close the dashboard and wait for the next scheduled time. Check that the log gained a new set of attempts and that the HTTPS snapshot contains those timestamps. Reopen the dashboard and compare the per-series timestamps with the snapshot.

Change both `REFRESH_SECONDS` and the cron expression if you want both schedules to use a different cadence. When updating the deployed code, update the checkout and dependencies on the scheduler host too.

## Refresh and failure behavior

- A **cache TTL** controls how long cached results can be reused. Expiration alone performs no retrieval. The app uses `st.cache_resource` for shared process state and an elapsed-time freshness check. Failed attempts are throttled by the same interval.
- The **active-session timer** uses `st.fragment` to check whether refresh is due. On initial load or when due, the app reads the HTTPS snapshot if configured. Fresh attempt timestamps allow it to use that snapshot. It fetches publishers directly when the snapshot is unavailable or due. **Refresh now** bypasses the interval and checks the publishers immediately.
- The **unattended schedule** runs the same retrieval code on the external host via cron. It works independently of Community Cloud’s uptime and viewer sessions. It writes the snapshot atomically and appends logs to `~/vietnam-economy-refresh.log`. The CLI returns a nonzero status for a partial failure while publishing the valid series and failure status.
- Each source’s success time advances only after a fetched response is parsed and validated. Unchanged observations qualify. A failed source check retains existing rows and their success time. The page-level timestamp is the most recent success across the cards. The cards show their own successes and attempts so partial failures remain visible.
- Dashboard checks maintain process-local state. If the Streamlit worker restarts, it can recover data from the scheduler snapshot. Without a snapshot, it fetches again and any previously collected bond closes held only in memory are lost. Manual UI checks do not write to the scheduler host.
- Observation periods, source publication or dataset-update dates when supplied, and UTC refresh timestamps are distinct. Rates and prices use the source currencies and units described below. The app performs no foreign-exchange conversion.

## Indicator sources and coverage

| Indicator | Series and method | Coverage in the verified run |
| --- | --- | --- |
| Real GDP growth | NSO quarterly `NGDP_R_PA_XDC`. Releases publish official headline growth figures. For a refreshable history, the app computes `100 × (GDP[t] / GDP[t−4] − 1)` from the official workbook. Both levels must come from the same quarterly sheet and price base. The 2026 Q3 amount is the individual quarter, as checked against the separate quarterly headline. The chart breaks at the switch from 2010 to 2020 constant prices. Workbook revisions can change earlier growth rates. [Workbook](https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/GDP_VNM.xlsx) | 2015 Q1–2026 Q3 |
| Core CPI inflation | NSO monthly `PCPICO_PC_PP_PT`. Uses the workbook’s published year-over-year percentage change. NSO excludes staple and fresh foods, energy, and administered items including education and health services. The workbook uses the 2019 CPI base through October 2025 and the 2024 base from November 2025. [Workbook](https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/CPI_VNM.xlsx), [NSO definition in the October release, footnote 3](https://www.nso.gov.vn/wp-content/uploads/2026/11/CPI-chi-so-gia-vang-va-chi-so-gia-do-la-My-thang-Muoi-va-10-thang-nam-2026.pdf) | August 2020–October 2026 |
| SBV refinancing rate | Monthly end-of-period `FIR_PA`, plus effective-date steps for Decisions 950 and 1123. The newer SBV Excel and SDMX links on the Vietnamese NSDP returned an error page in testing. The app can recheck the historical workbook and announcements. A dependable feed for the current policy rate remains unavailable, and the dashboard labels this limitation. [NSDP](https://nsdp.nso.gov.vn/), [archive](https://nsdp.nso.gov.vn/GSO-chung/tieng%20anh/excel/sbv/INR_Vietnam%202019.xlsx), [May decision](https://en.baochinhphu.vn/sbv-continues-cutting-regulatory-interest-rates-111230524155951848.htm), [June decision](https://en.baochinhphu.vn/central-bank-cuts-regulatory-interest-rate-111230616172123401.htm) | January 2015–May 2019 month-ends, plus two 2023 decisions |
| 10-year government bond yield | ADB AsianBondsOnline’s Vietnam VND 10-year secondary-market closing yield to maturity. ADB is the public publisher of this display and identifies Bloomberg as its primary Market Watch supplier. HNX’s transaction page was unreachable from the test host, and a suitable historical benchmark download was unverified. The app reads ADB’s public Market Watch table and accumulates captured closes. An outage can leave a gap in history. [Market Watch](https://asianbondsonline.adb.org/vietnam/), [methodology](https://asianbondsonline.adb.org/market_watch_specs.php?market=country#govt_bonds) | One close at initial load |
| Coffee price | The NSO trade workbook reviewed for Vietnam provides coffee export values and quantities, which could produce an export unit value. A refreshable Vietnamese farm-gate price history was unverified. The World Bank Pink Sheet provides the ICO Robustas benchmark, an international ex-dock measure covering New York and Le Havre/Marseilles. The app uses the monthly nominal USD/kg column directly. [World Bank download page](https://www.worldbank.org/en/research/commodity-markets), [ICO definitions and market data](https://ico.org/resources/public-market-information/) | January 2015–October 2026 |
| Merchandise trade balance | Official NSO merchandise summary, `TXG_FOB_USD` minus `TMG_CIF_USD`, divided by 1,000 to express USD billions. Both legs use the same table, period and unadjusted reporting basis. Exports are FOB and imports are CIF. Positive values are surpluses. The workbook flags January 2018 onward as preliminary. Months without a usable pair remain gaps. [Workbook](https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/MET_VNM.xlsx) | 118 paired months, January 2015–October 2026 |
| National working-age unemployment | NSO `LUR_PT`, national percentage of the working-age labor force who were without work, seeking and available. The lower threshold is age 15, and the upper limit follows the applicable working-age definition. The statutory normal retirement ages in 2026 were 61 years 6 months for men and 57 years for women. The workbook marks 2026 Q3 as preliminary. [Workbook](https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/LMI_VNM.xlsx), [retirement-age schedule](https://baochinhphu.vn/quy-dinh-moi-ve-tuoi-nghi-huu-102283105.htm) | 2016 Q1–2026 Q3 |
| Gini index | World Bank WDI `SI.POV.GINI`, household consumption inequality on a 0–100 scale. The PIP records identify national household survey observations and `welfare_type=consumption`. Null years stay empty and the chart uses points. [WDI series](https://data.worldbank.org/indicator/SI.POV.GINI?locations=VN), [PIP record for 2022](https://api.worldbank.org/pip/v1/pip?country=VNM&year=2022&povline=3&fill_gaps=false) | Survey years 2016, 2018, 2020 and 2022 |

## Observation verification

Dashboard values below come from the real refresh. The interface displays three decimals. Calculations use full available source precision.

| Indicator | Publisher and original source | Observation | Published value | Dashboard value and comparison |
| --- | --- | --- | --- | --- |
| Real GDP growth | [NSO quarterly report](https://www.nso.gov.vn/tin-tuc-thong-ke/2026/10/bao-cao-tinh-hinh-kinh-te-xa-hoi-quy-iii-va-9-thang-nam-2026/) | 2026 Q3 | 9.95% YoY, estimated | 9.948% YoY. Calculated from 3,004,778.442597 and 2,732,908.972376 billion VND at 2020 prices. Rounds to 9.95%. |
| Core CPI inflation | [NSO October CPI release, table 2](https://www.nso.gov.vn/wp-content/uploads/2026/11/CPI-chi-so-gia-vang-va-chi-so-gia-do-la-My-thang-Muoi-va-10-thang-nam-2026.pdf) | October 2026 | 4.04% YoY | 4.036% YoY from the workbook’s monthly column, rounding to 4.04%. |
| Refinancing rate | [Vietnam Government Portal reporting SBV Decision 1123](https://en.baochinhphu.vn/central-bank-cuts-regulatory-interest-rate-111230616172123401.htm) | Effective June 19, 2023 | 4.5% per year | 4.500% per year, same effective date. Verification covers this decision. Current-rate verification is pending because the newer linked SBV feeds returned error pages. |
| 10-year yield | [ADB Vietnam Market Watch](https://asianbondsonline.adb.org/vietnam/) and [ADB yield curve view](https://asianbondsonline.adb.org/charts/government_bond_yields.php?economies=VN) | November 18, 2026 close | 4.462% per year | 4.462% per year, directly from the 10-year cell. The live Market Watch link rolls forward. |
| Coffee | [World Bank Pink Sheet monthly workbook used in the run](https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx), `Monthly Prices`, `Coffee, Robusta` | October 2026 | USD 3.63/kg | USD 3.630/kg, directly from the World Bank workbook. The [ICO report](https://ico.org/documents/cy2026-27/cmr-1026-e.pdf) separately shows 164.19 US cents/lb, approximately USD 3.620/kg. The publications differ by about USD 0.01/kg for this month. The dashboard uses the World Bank vintage consistently. |
| Trade balance | [NSO October report](https://www.nso.gov.vn/en/data-and-statistics/2026/11/report-socio-economic-performance-in-october-and-10-months-of-2026/) | October 2026 | USD 7.31 billion surplus, preliminary | USD 7.307 billion, from `(64,670.426411 − 57,363.689155) / 1,000`. Rounds to USD 7.31 billion. |
| Unemployment | [NSO quarterly press release, labor section](https://www.nso.gov.vn/du-lieu-va-so-lieu-thong-ke/2026/10/thong-cao-bao-chi-tinh-hinh-kinh-te-xa-hoi-quy-iii-va-9-thang-nam-2026/) | 2026 Q3 | 2.22% of working-age labor force | 2.218%, rounding to 2.22%. The workbook flags it as preliminary. |
| Gini | [World Bank WDI](https://data.worldbank.org/indicator/SI.POV.GINI?locations=VN) and [PIP country profile](https://pip.worldbank.org/country-profiles/VNM) | 2022 survey | WDI 36.1 on 0–100. PIP 36.09. | 36.100 from WDI. The more precise PIP figure rounds to 36.1 at WDI precision. |

## Execution evidence

Python 3.12.14, using the pinned dependencies. The following is stdout from a real local run of `python app.py --refresh clean.json` against live publishers, with a new snapshot path. UTC timestamps correspond to the evening of November 19, 2026 in U.S. Central time. Counts show observations loaded from January 2015 onward. The policy result confirms retrieval of the archive and decisions.

```text
2026-11-20T01:07:40+00:00 gdp [NSO] OK observations=47 latest=2026-Q3
2026-11-20T01:07:39+00:00 core [NSO] OK observations=75 latest=2026-10
2026-11-20T01:07:44+00:00 policy [SBV archive + Government Portal] OK limited archive observations=55 latest=2023-06-19
2026-11-20T01:07:34+00:00 bond [ADB AsianBondsOnline] OK observations=1 latest=2026-11-18
2026-11-20T01:07:35+00:00 coffee [World Bank / ICO] OK observations=142 latest=2026-10
2026-11-20T01:07:41+00:00 trade [NSO] OK observations=118 latest=2026-10
2026-11-20T01:07:55+00:00 jobs [NSO] OK observations=43 latest=2026-Q3
2026-11-20T01:07:40+00:00 gini [World Bank WDI] OK observations=4 latest=2022
```

Streamlit’s local `AppTest` rendered eight charts without exceptions and exercised the manual refresh button, which ran a second live publisher check. `pip check` found no dependency conflicts. The six-hour fragment timing was not observed over a full interval. The CLI retrieval and snapshot write were verified locally. Cron execution, public HTTPS serving, and Community Cloud retrieval and restart recovery remain pending because the scheduler host and Community Cloud app were not provisioned.
