import argparse
import calendar
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urljoin, urlparse

import altair as alt
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openpyxl import load_workbook

NSO = "https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/"
LEGACY = ("https://nsdp.nso.gov.vn/GSO-chung/tieng%20anh/excel/sbv/"
          "INR_Vietnam%202019.xlsx")
DECISION = "https://en.baochinhphu.vn/central-bank-cuts-regulatory-interest-rate-111230616172123401.htm"
MAY = "https://en.baochinhphu.vn/sbv-continues-cutting-regulatory-interest-rates-111230524155951848.htm"
ADB = "https://asianbondsonline.adb.org/vietnam/"
PINK = "https://www.worldbank.org/en/research/commodity-markets"
GINI = "https://data.worldbank.org/indicator/SI.POV.GINI?locations=VN"
WDI = "https://api.worldbank.org/v2/country/VNM/indicator/SI.POV.GINI"
START = date(2015, 1, 1)

# Title, display unit, frequency, publisher link, series ID, methodology.
META = {
    "gdp": ("Real GDP growth", "% YoY", "Quarterly", NSO + "GDP_VNM.xlsx",
            "NGDP_R_PA_XDC",
            "Individual quarters. 100 × (real GDP / real GDP four quarters earlier − 1). "
            "Same sheet and constant-price base for both levels. NSO workbook vintages "
            "use 2010 and 2020 prices. Estimates can be revised."),
    "core": ("Core CPI inflation", "% YoY", "Monthly", NSO + "CPI_VNM.xlsx",
             "PCPICO_PC_PP_PT",
             "NSO's published year-on-year rate. Excludes staple foods, fresh foods, "
             "energy and administered items including health and education services. "
             "Workbook switches CPI bases from 2019 to 2024 in November 2025."),
    "policy": ("State Bank of Vietnam refinancing rate", "% per year",
               "Monthly archive / decision dates", LEGACY, "FIR_PA / 950 and 1123/QD-NHNN",
               "Archived month-end observations plus May and June 2023 effective-date decisions. "
               "Coverage is incomplete. The step ends at the last verified decision."),
    "bond": ("Vietnam 10-year government bond yield", "% per year",
             "Daily market close, captured dates", ADB, "Market Watch · 10 Year",
             "ADB AsianBondsOnline VND secondary-market closing yield to maturity. "
             "ADB primarily sources Market Watch from Bloomberg. Only captured closes are shown."),
    "coffee": ("Coffee price · ICO Robustas benchmark", "USD/kg", "Monthly", PINK,
               "Pink Sheet · Coffee, Robusta",
               "World Bank publishes the ICO Robustas indicator, averaging New York "
               "and Le Havre/Marseilles ex-dock markets. This international price "
               "represents a different market from Vietnam farm-gate prices. "
               "Published nominal USD/kg is used directly."),
    "trade": ("Merchandise trade balance", "USD billion", "Monthly", NSO + "MET_VNM.xlsx",
              "TXG_FOB_USD − TMG_CIF_USD",
              "NSO merchandise exports FOB minus imports CIF, both unadjusted for "
              "seasonality and from the same table. USD millions ÷ 1,000. "
              "Positive means surplus. The workbook flags figures from January 2018 as preliminary."),
    "jobs": ("National working-age unemployment rate", "% of labor force",
             "Quarterly", NSO + "LMI_VNM.xlsx", "LUR_PT",
             "NSO's national working-age labor force under the applicable Vietnamese "
             "working-age limits, starting at age 15 with upper limits tied to statutory "
             "retirement age. Unemployed people are without work, seeking and available. "
             "Workbook ** markers indicate preliminary estimates."),
    "gini": ("Consumption Gini index", "Index (0–100)", "Survey years", GINI,
             "SI.POV.GINI",
             "World Bank WDI / Poverty and Inequality Platform, household consumption "
             "inequality. Zero is perfect equality and 100 is maximum inequality. "
             "Only non-null survey years are plotted."),
}
LIMITS = dict(zip(META, [(-50, 50), (-30, 100), (0, 50), (0, 50),
                        (0.01, 100), (-100, 100), (0, 100), (0, 100)]))


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(url, **params):
    response = requests.get(url, params=params, timeout=(10, 35))
    response.raise_for_status()
    return response


def workbook(url):
    return load_workbook(BytesIO(fetch(url).content), read_only=True, data_only=True)


def soup(url):
    return BeautifulSoup(fetch(url).content, "html.parser")


def one(items):
    result, = items
    return result


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def period_date(period):
    if len(period) == 10:
        return date.fromisoformat(period)
    year = int(period[:4])
    if len(period) == 4:
        return date(year, 12, 31)
    month = int(period[-1]) * 3 if "-Q" in period else int(period[5:7])
    return date(year, month, calendar.monthrange(year, month)[1])


def point(period, value, note="", group="", publication=None):
    return {"period": period, "date": period_date(period).isoformat(), "value": value,
            "note": note, "group": group, "publication": publication}


def table(sheet, code, column=2):
    rows = list(sheet.iter_rows(values_only=True))
    header = one(r for r in rows if r[0] == "Country code")
    row = one(r for r in rows if r[column] == code)
    return header, row


def cells(header, row, pattern):
    for label, value in zip(header, row):
        if isinstance(label, str) and re.fullmatch(pattern, label) and number(value):
            yield label, value


def load_gdp(url):
    results = {}
    with_book = workbook(url)
    try:
        for sheet in with_book:
            if not sheet.title.startswith("Dataset_Q"):
                continue
            header, row = table(sheet, "NGDP_R_PA_XDC")
            base = row[5]
            if row[4] != "Q" or base not in (2010, 2020):
                raise ValueError("Unexpected GDP frequency or constant-price base")
            levels = dict(cells(header, row, r"\d{4}-Q[1-4]"))
            for period, level in levels.items():
                previous = levels.get(f"{int(period[:4]) - 1}{period[4:]}")
                if previous and level > 0 and previous > 0:
                    results.setdefault(base, {})[period] = point(
                        period, 100 * (level / previous - 1), f"{base} constant prices", str(base))
    finally:
        with_book.close()
    return list({**results.get(2010, {}), **results.get(2020, {})}.values())


def load_core(url):
    book = workbook(url)
    try:
        values = {}
        for sheet in sorted(book, key=lambda tab: tab.title):
            if not re.fullmatch(r"Dataset base \d{4}", sheet.title):
                continue
            header, row = table(sheet, "PCPICO_PC_PP_PT", 3)
            if not re.search(r"Y/Y", row[1]):
                raise ValueError("Unexpected core CPI definition")
            values.update({k: point(k, v, sheet.title, sheet.title)
                           for k, v in cells(header, row, r"\d{4}-\d{2}")})
        return list(values.values())
    finally:
        book.close()


def load_policy(url):
    book = workbook(url)
    try:
        header, row = table(book["Dataset"], "FIR_PA")
        if row[1].strip() != "Refinancing Rate":
            raise ValueError("Unexpected policy rate definition")
        rows = [point(k, v, "Historical month-end", "archive")
                for k, v in cells(header, row, r"\d{4}-\d{2}")]
    finally:
        book.close()
    article = " ".join(soup(DECISION).get_text(" ", strip=True).split())
    rate = re.search(r"refinancing interest rate will be lowered to ([\d.]+) percent", article)
    if not rate or "effective from June 19, 2023" not in article or "1123/QD-NHNN" not in article:
        raise ValueError("Could not validate the decision text")
    may = " ".join(soup(MAY).get_text(" ", strip=True).split())
    earlier = re.search(r"Refunding interest rate shall be slashed from [\d.]+ percent per year to ([\d.]+) percent", may)
    if not earlier or "effective from May 25" not in may or "950/QD-NHNN" not in may:
        raise ValueError("Could not validate the May decision text")
    rows.append(point("2023-05-25", float(earlier[1]), "Effective date, Decision 950",
                      "decision", "2023-05-24"))
    return rows + [point("2023-06-19", float(rate[1]), "Effective date, Decision 1123",
                         "decision", "2023-06-16")]


def load_bond(url):
    table_html = one(t for t in soup(url).find_all("table")
                     if "Govt. Bond Yields" in t.get_text(" ", strip=True)
                     and "Latest Yield" in t.get_text(" ", strip=True))
    closing = re.search(r"Close of\s+([A-Za-z]+ \d{1,2}, \d{4})",
                        table_html.get_text(" ", strip=True))
    row = one(c for tr in table_html.find_all("tr")
              if (c := [td.get_text(strip=True) for td in tr.find_all("td")])
              and c[0] == "10 Year")
    day = datetime.strptime(closing[1], "%B %d, %Y").date().isoformat()
    return [point(day, float(row[1]), "Market close")]


def load_coffee(url):
    links = {urljoin(url, a["href"]) for a in soup(url).select("a[href]")
             if a["href"].endswith("/CMO-Historical-Data-Monthly.xlsx")}
    book = workbook(one(links))
    try:
        rows = list(book["Monthly Prices"].values)
        col = rows[4].index("Coffee, Robusta")
        if rows[5][col] != "($/kg)":
            raise ValueError("Unexpected coffee unit")
        updated = datetime.strptime(rows[3][0], "Updated on %B %d, %Y").date().isoformat()
        return [point(f"{r[0][:4]}-{r[0][-2:]}", r[col], publication=updated)
                for r in rows[6:] if re.fullmatch(r"\d{4}M\d{2}", str(r[0])) and number(r[col])]
    finally:
        book.close()


def load_trade(url):
    book = workbook(url)
    try:
        sheet = book["Value"]
        records = list(sheet.values)
        header = one(r for r in records if r[0] == "Country code")
        summary = next(i for i, r in enumerate(records) if r[1] == "Merchandise Trade Summary")
        exports = one(r for r in records[summary + 1:summary + 4] if r[2] == "TXG_FOB_USD")
        imports = one(r for r in records[summary + 1:summary + 4] if r[2] == "TMG_CIF_USD")
        status = one(r for r in records if r[0] == "COMMENT")
        if not all("Mil.US$" in r[1] for r in (exports, imports)):
            raise ValueError("Unexpected merchandise trade unit")
        rows = []
        for p, x, m, flag in zip(header, exports, imports, status):
            if re.fullmatch(r"\d{4}-\d{2}", str(p)) and number(x) and number(m) and x > 0 and m > 0:
                preliminary = "prel" in str(flag).lower() or (
                    "Jan 2018 onward are preliminary" in str(status[1]) and p >= "2018-01")
                rows.append(point(p, (x - m) / 1000, "Preliminary" if preliminary else ""))
        return rows
    finally:
        book.close()


def load_jobs(url):
    book = workbook(url)
    try:
        header, row = table(book["Dataset_LMI"], "LUR_PT")
        if "Unemployment Rate at working age" != row[1].strip():
            raise ValueError("Unexpected unemployment population")
        return [point(k.rstrip("*"), v, "Preliminary" if k.endswith("**") else "")
                for k, v in cells(header, row, r"\d{4}-Q[1-4]\**")]
    finally:
        book.close()


def load_gini(_):
    meta, rows = fetch(WDI, format="json", per_page=100, source=2).json()
    if int(meta["pages"]) != 1 or any(
        r["countryiso3code"] != "VNM" or r["indicator"]["id"] != "SI.POV.GINI" for r in rows
    ):
        raise ValueError("Unexpected WDI response")
    return [point(r["date"], r["value"], publication=meta.get("lastupdated"))
            for r in rows if r["value"] is not None]


LOADERS = dict(zip(META, [load_gdp, load_core, load_policy, load_bond, load_coffee,
                          load_trade, load_jobs, load_gini]))
PUBLISHERS = ("NSO", "NSO", "SBV archive + Government Portal", "ADB AsianBondsOnline",
              "World Bank / ICO", "NSO", "NSO", "World Bank WDI")


def validate(key, rows):
    lower, upper = LIMITS[key]
    dates = [date.fromisoformat(r["date"]) for r in rows]
    if not dates or len(set(dates)) != len(dates):
        raise ValueError("Empty series or duplicate observation dates")
    if any(d > datetime.now(timezone.utc).date() for d in dates):
        raise ValueError("Future observation date")
    if any(not number(r["value"]) or not lower <= r["value"] <= upper for r in rows):
        raise ValueError("Observation outside validation bounds")
    return sorted(rows, key=lambda r: r["date"])


def check(key, old):
    try:
        loaded = [r for r in LOADERS[key](META[key][3]) if r["date"] >= START.isoformat()]
        fresh = validate(key, loaded)
        previous = old.get("rows", [])
        if previous and fresh[-1]["date"] < previous[-1]["date"]:
            raise ValueError("Source returned an older latest observation")
        if key == "bond":
            fresh = validate(key, list({r["date"]: r for r in previous + fresh}.values()))
        now = stamp()
        return {"rows": fresh, "success": now, "attempt": now, "error": None}
    except Exception as exc:
        # Network exceptions may include URLs. Keep status text free of request details.
        message = type(exc).__name__
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            message += f" {exc.response.status_code}"
        return {**old, "attempt": stamp(), "error": message}


def refresh(state):
    with ThreadPoolExecutor(max_workers=4) as workers:
        records = workers.map(lambda key: check(key, state.get(key, {})), META)
        result = dict(zip(META, records))
    for (key, entry), publisher in zip(result.items(), PUBLISHERS):
        rows = entry.get("rows", [])
        latest = rows[-1]["period"] if rows else "-"
        outcome = f"ERROR {entry['error']}" if entry["error"] else (
            "OK limited archive" if key == "policy" else "OK")
        print(f"{entry['attempt']} {key} [{publisher}] {outcome} "
              f"observations={len(rows)} latest={latest}", flush=True)
    return result


def snapshot(text):
    raw = json.loads(text)
    if raw["version"] != 1 or set(raw["series"]) != set(META):
        raise ValueError("Unexpected snapshot schema")
    for key, entry in raw["series"].items():
        if entry.get("rows"):
            validate(key, entry["rows"])
    return raw["series"]


def scheduled(path):
    previous = snapshot(path.read_text()) if path.exists() else {}
    state = refresh(previous)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"version": 1, "series": state}, allow_nan=False))
    temporary.chmod(0o644)
    temporary.replace(path)
    return int(any(entry["error"] for entry in state.values()))


@st.cache_resource
def runtime():
    return {"lock": Lock(), "state": {}, "checked": None, "warning": ""}


def online(seconds, force):
    view = runtime()
    with view["lock"]:
        if force or view["checked"] is None or monotonic() - view["checked"] >= seconds:
            state = view["state"]
            url = os.getenv("SNAPSHOT_URL")
            view["warning"] = ""
            if url:
                try:
                    if urlparse(url).scheme != "https":
                        raise ValueError("HTTPS snapshot required")
                    remote = snapshot(fetch(url).text)
                    for key, entry in remote.items():
                        local = state.get(key, {})
                        newest = max((local, entry), key=lambda item: item.get("attempt", ""))
                        valid = max((local, entry), key=lambda item: item.get("success", ""))
                        if key == "bond" and valid:
                            combined = {r["date"]: r for item in (local, entry)
                                        for r in item.get("rows", [])}
                            valid = {**valid, "rows": validate(key, list(combined.values()))}
                        state[key] = {**valid, "attempt": newest.get("attempt", ""),
                                      "error": newest.get("error")}
                except (requests.RequestException, ValueError, KeyError, TypeError):
                    view["warning"] = "Scheduled snapshot unavailable. Publisher checks use this process."
            cutoff = datetime.now(timezone.utc).timestamp() - seconds
            recent_schedule = url and not view["warning"] and all(
                state.get(k, {}).get("attempt") and
                datetime.fromisoformat(state[k]["attempt"]).timestamp() >= cutoff for k in META
            )
            if force or not recent_schedule:
                view["state"] = refresh(state)
            view["checked"] = monotonic()
        return dict(view["state"]), view["warning"]


def chart(key, entry, first, last):
    title, unit, frequency, url, code, definition = META[key]
    rows = entry.get("rows", [])
    latest = rows[-1] if rows else {}
    st.subheader(title)
    st.caption(f"{unit} · {frequency} · Latest observation {latest.get('period', '—')}")
    if latest:
        st.markdown(f"**{latest['value']:,.3f} {unit}**")
    if entry.get("error"):
        st.warning(f"Refresh failed ({entry['error']}) · "
                   + ("last valid data shown" if rows else "no valid data available"))
    elif key == "policy":
        st.warning("Historical coverage only · current rate refresh unavailable")
    else:
        st.caption("Source check succeeded" if rows else "Waiting for data")
    st.caption(f"Last success {entry.get('success', '—')} · Last attempt {entry.get('attempt', '—')}")
    st.caption(f"Publisher release / update {latest.get('publication') or 'unavailable in feed'}"
               + (f" · {latest['note']}" if latest.get("note") else ""))
    visible = [r for r in rows if first <= r["date"] <= last]
    if visible:
        previous = None
        segment = 0
        chart_rows = []
        for row in visible:
            observed = date.fromisoformat(row["date"])
            if previous and key in ("gdp", "jobs", "core", "trade", "coffee"):
                if (observed - previous).days > (110 if key in ("gdp", "jobs") else 45):
                    segment += 1
            chart_rows.append({**row, "line": f"{row['group']}/{segment}"})
            previous = observed
        base = alt.Chart(alt.Data(values=chart_rows)).encode(
            x=alt.X("date:T", title=None, scale=alt.Scale(domain=[first, last])),
            y=alt.Y("value:Q", title=unit, scale=alt.Scale(zero=key == "gini")),
            tooltip=[alt.Tooltip("period:N", title="Observation"),
                     alt.Tooltip("value:Q", title=unit, format=".3f"),
                     alt.Tooltip("note:N", title="Source note")])
        marks = base.mark_point(filled=True, color="#087e74", size=36)
        if key not in ("bond", "gini"):
            line = base.mark_line(color="#087e74",
                                  interpolate="step-after" if key == "policy" else "linear")
            marks = line.encode(detail="line:N") + marks
        st.altair_chart(marks.properties(height=210), width="stretch")
    else:
        st.info("No observations in the selected range.")
    st.markdown(f"[Publisher source]({url}) · `{code}`")
    if key == "policy":
        st.markdown(f"[May decision]({MAY}) · [June decision]({DECISION})")
    st.caption(definition)


def dashboard():
    st.set_page_config(page_title="Vietnam’s economy", layout="wide")
    st.title("Vietnam’s economy")
    seconds = max(10, int(os.getenv("REFRESH_SECONDS", "21600")))
    today = datetime.now(timezone.utc).date()
    dates = st.slider("Observation date range", START, today, (START, today),
                      format="MMM YYYY")

    @st.fragment(run_every=min(seconds, 60))
    def panel():
        force = st.button("Refresh now", type="primary")
        state, warning = online(seconds, force)
        latest = max((x.get("success", "") for x in state.values()), default="")
        st.caption(f"Most recent successful source refresh {latest or '—'} · UTC")
        if warning:
            st.warning(warning)
        for index, key in enumerate(META):
            if index % 2 == 0:
                columns = st.columns(2)
            with columns[index % 2], st.container(border=True):
                chart(key, state.get(key, {}), *(d.isoformat() for d in dates))

    panel()


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--refresh", required=True, type=Path, metavar="SNAPSHOT")
        raise SystemExit(scheduled(parser.parse_args().refresh))
    dashboard()
