import argparse
import calendar
import csv
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urljoin, urlparse

import altair as alt
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

NSO = "https://nsdp.nso.gov.vn/GSO-chung/Tu%E1%BA%A5n%20Anh/excel%20chung/"
ARCHIVE = "https://nsdp.nso.gov.vn/GSO-chung/tieng%20anh/excel/"
OLD_CORE = ARCHIVE + "gso/TKG.%20CPI_Vietnam.xlsx"
LEGACY = ARCHIVE + "sbv/INR_Vietnam%202019.xlsx"
JUNE = "https://en.baochinhphu.vn/central-bank-cuts-regulatory-interest-rate-111230616172123401.htm"
MAY = "https://en.baochinhphu.vn/sbv-continues-cutting-regulatory-interest-rates-111230524155951848.htm"
ADB = "https://asianbondsonline.adb.org/vietnam/"
ADB_CURVE = "https://asianbondsonline.adb.org/charts/government_bond_yields.php?economies=VN"
PINK = "https://www.worldbank.org/en/research/commodity-markets"
FX = "https://data.bis.org/topics/XRU/BIS%2CWS_XRU%2C1.0/M.VN.VND.E"
FX_API = "https://stats.bis.org/api/v1/data/BIS,WS_XRU,1.0/M.VN.VND.E"
START = date(2015, 1, 1)

# Name, unit, frequency, source, series ID, methodology.
META = {
    "gdp": (
        "Real GDP growth", "% YoY", "Quarterly", NSO + "GDP_VNM.xlsx",
        "NGDP_R_PA_XDC",
        "Individual quarters. 100 × (real GDP / real GDP four quarters earlier − 1). "
        "Both levels come from the same quarterly sheet and constant-price base. "
        "The NSO workbook uses 2010 and 2020 price bases. Estimates can be revised.",
    ),
    "core": (
        "Core CPI inflation", "% YoY", "Monthly", NSO + "CPI_VNM.xlsx",
        "PCPICO_BY_CP_A_PT / PCPICO_PC_PP_PT",
        "NSO's published year-on-year rate. It excludes staple foods, fresh foods, "
        "energy and administered items including health and education services. "
        "The archive and monthly releases extend coverage to April 2015. "
        "The current workbook switches from the 2019 to the 2024 CPI base in November 2025.",
    ),
    "policy": (
        "State Bank of Vietnam refinancing rate", "% per year",
        "Monthly archive and decision dates", LEGACY, "FIR_PA / SBV decisions",
        "Archived month-end rates and verified effective-date changes from 2019 to 2023. "
        "The step ends at the last verified decision. The live check rechecks the archive.",
    ),
    "bond": (
        "Vietnam 10-year government bond yield", "% per year",
        "Captured market closes and period-end references", ADB, "Market Watch · 10 Year",
        "ADB AsianBondsOnline VND secondary-market closing yield to maturity. "
        "ADB primarily sources Market Watch from Bloomberg. "
        "The chart contains verified reference points and closes captured by the app.",
    ),
    "coffee": (
        "Coffee price · ICO Robustas benchmark", "USD/kg", "Monthly", PINK,
        "Pink Sheet · Coffee, Robusta",
        "World Bank's nominal USD/kg series for the ICO Robustas indicator. "
        "The international benchmark averages New York and Le Havre/Marseilles "
        "ex-dock markets. It represents a different market from Vietnam farm-gate prices.",
    ),
    "trade": (
        "Merchandise trade balance", "USD billion", "Monthly", NSO + "MET_VNM.xlsx",
        "TXG_FOB_USD − TMG_CIF_USD",
        "NSO merchandise exports FOB minus imports CIF, both unadjusted and from the "
        "same table. USD millions ÷ 1,000. Positive means surplus. The workbook flags "
        "figures from January 2018 as preliminary. Months without a usable pair stay empty.",
    ),
    "jobs": (
        "National working-age unemployment rate", "% of labor force", "Quarterly",
        NSO + "LMI_VNM.xlsx", "LUR_PT",
        "Share of the national working-age labor force who are without work, seeking "
        "and available. The lower threshold is 15 and the applicable upper limits "
        "are tied to statutory retirement age. Workbook ** marks preliminary data.",
    ),
    "fx": (
        "USD to Vietnamese dong", "VND per USD", "Monthly, end of period", FX,
        "BIS WS_XRU · M.VN.VND.E",
        "Vietnamese dong per US dollar at month-end, published by the BIS. "
        "A rise means a weaker dong against the dollar. Used directly.",
    ),
}
LIMITS = {
    "gdp": (-50, 50), "core": (-30, 100), "policy": (0, 50), "bond": (0, 50),
    "coffee": (0.01, 100), "trade": (-100, 100), "jobs": (0, 100), "fx": (1000, 100000),
}
PUBLISHERS = {
    "gdp": "NSO", "core": "NSO", "policy": "SBV archive + Government Portal",
    "bond": "ADB AsianBondsOnline", "coffee": "World Bank / ICO",
    "trade": "NSO", "jobs": "NSO", "fx": "BIS",
}
RETAIN_HISTORY = {"core", "policy", "bond"}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(url, *, headers=None, **params):
    retry = Retry(
        total=2, connect=2, read=1, status=2, backoff_factor=0.6,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        respect_retry_after_header=False, raise_on_status=False,
    )
    try:
        with requests.Session() as session:
            session.mount("https://", HTTPAdapter(max_retries=retry))
            response = session.get(url, params=params, headers=headers, timeout=(6, 35))
            response.raise_for_status()
            return response
    except requests.RequestException as exc:
        host = urlparse(url).hostname
        if isinstance(exc, requests.HTTPError):
            reason = f"HTTP {exc.response.status_code}"
        elif isinstance(exc, requests.Timeout):
            reason = "request timed out"
        elif isinstance(exc, requests.exceptions.SSLError):
            reason = "TLS verification failed"
        else:
            reason = "connection could not be established"
        raise ValueError(f"{host}: {reason}") from exc


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
    return {
        "period": period, "date": period_date(period).isoformat(), "value": value,
        "note": note, "group": group, "publication": publication,
    }


def table(sheet, code, column=2):
    rows = list(sheet.iter_rows(values_only=True))
    return (
        one(r for r in rows if r[0] == "Country code"),
        one(r for r in rows if r[column] == code),
    )


def cells(header, row, pattern):
    for label, value in zip(header, row):
        if isinstance(label, str) and re.fullmatch(pattern, label) and number(value):
            yield label, value


def load_gdp(url):
    results = {}
    book = workbook(url)
    try:
        for sheet in book:
            if not sheet.title.startswith("Dataset_Q"):
                continue
            header, row = table(sheet, "NGDP_R_PA_XDC")
            base = row[5]
            if row[4] != "Q" or base not in (2010, 2020):
                raise ValueError("Unexpected GDP frequency or price base")
            levels = dict(cells(header, row, r"\d{4}-Q[1-4]"))
            for period, value in levels.items():
                previous = levels.get(f"{int(period[:4]) - 1}{period[4:]}")
                if previous and value > 0 and previous > 0:
                    results.setdefault(base, {})[period] = point(
                        period, 100 * (value / previous - 1),
                        f"{base} constant prices", str(base),
                    )
        return list({**results.get(2010, {}), **results.get(2020, {})}.values())
    finally:
        book.close()


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
            for period, value in cells(header, row, r"\d{4}-\d{2}"):
                values[period] = point(period, value, sheet.title, sheet.title)
        return list(values.values())
    finally:
        book.close()


def load_policy(url):
    book = workbook(url)
    try:
        header, row = table(book["Dataset"], "FIR_PA")
        if row[1].strip() != "Refinancing Rate":
            raise ValueError("Unexpected policy rate definition")
        return [
            point(k, v, "Historical month-end", "archive")
            for k, v in cells(header, row, r"\d{4}-\d{2}")
        ]
    finally:
        book.close()


def load_bond(url):
    table_html = one(
        t for t in soup(url).find_all("table")
        if "Govt. Bond Yields" in t.get_text(" ", strip=True)
        and "Latest Yield" in t.get_text(" ", strip=True)
    )
    closing = re.search(
        r"Close of\s+([A-Za-z]+ \d{1,2}, \d{4})",
        table_html.get_text(" ", strip=True),
    )
    if not closing:
        raise ValueError("Missing ADB closing date")
    row = one(
        columns for tr in table_html.find_all("tr")
        if (columns := [td.get_text(strip=True) for td in tr.find_all("td")])
        and columns[0] == "10 Year"
    )
    day = datetime.strptime(closing[1], "%B %d, %Y").date().isoformat()
    return [point(day, float(row[1]), "Market close")]


def load_coffee(url):
    links = {
        urljoin(url, a["href"]) for a in soup(url).select("a[href]")
        if a["href"].endswith("/CMO-Historical-Data-Monthly.xlsx")
    }
    book = workbook(one(links))
    try:
        rows = list(book["Monthly Prices"].values)
        col = rows[4].index("Coffee, Robusta")
        if rows[5][col] != "($/kg)":
            raise ValueError("Unexpected coffee unit")
        updated = datetime.strptime(rows[3][0], "Updated on %B %d, %Y").date().isoformat()
        return [
            point(f"{r[0][:4]}-{r[0][-2:]}", r[col], publication=updated)
            for r in rows[6:] if re.fullmatch(r"\d{4}M\d{2}", str(r[0])) and number(r[col])
        ]
    finally:
        book.close()


def load_trade(url):
    book = workbook(url)
    try:
        records = list(book["Value"].values)
        header = one(r for r in records if r[0] == "Country code")
        summary = next(i for i, r in enumerate(records) if r[1] == "Merchandise Trade Summary")
        exports = one(r for r in records[summary + 1:summary + 4] if r[2] == "TXG_FOB_USD")
        imports = one(r for r in records[summary + 1:summary + 4] if r[2] == "TMG_CIF_USD")
        status = one(r for r in records if r[0] == "COMMENT")
        if not all("Mil.US$" in r[1] for r in (exports, imports)):
            raise ValueError("Unexpected merchandise trade unit")
        rows = []
        for period, x, m, flag in zip(header, exports, imports, status):
            if not re.fullmatch(r"\d{4}-\d{2}", str(period)):
                continue
            if not (number(x) and number(m) and x > 0 and m > 0):
                continue
            preliminary = "prel" in str(flag).lower() or (
                "Jan 2018 onward are preliminary" in str(status[1]) and period >= "2018-01"
            )
            rows.append(point(period, (x - m) / 1000, "Preliminary" if preliminary else ""))
        return rows
    finally:
        book.close()


def load_jobs(url):
    book = workbook(url)
    try:
        header, row = table(book["Dataset_LMI"], "LUR_PT")
        if row[1].strip() != "Unemployment Rate at working age":
            raise ValueError("Unexpected unemployment population")
        return [
            point(k.rstrip("*"), v, "Preliminary" if k.endswith("**") else "")
            for k, v in cells(header, row, r"\d{4}-Q[1-4]\**")
        ]
    finally:
        book.close()


def load_fx(_):
    response = fetch(
        FX_API, startPeriod="2015-01",
        headers={"Accept": "application/vnd.sdmx.data+csv;version=1.0.0"},
    )
    rows = []
    for record in csv.DictReader(StringIO(response.text.lstrip("\ufeff"))):
        dimensions = tuple(record[field] for field in ("FREQ", "REF_AREA", "CURRENCY", "COLLECTION"))
        if dimensions != ("M", "VN", "VND", "E") or record["UNIT_MULT"] != "0":
            raise ValueError("Unexpected BIS exchange-rate series")
        if record["OBS_VALUE"]:
            note = "Provisional" if record.get("OBS_STATUS") == "P" else ""
            rows.append(point(record["TIME_PERIOD"], float(record["OBS_VALUE"]), note))
    return rows


LOADERS = {
    "gdp": load_gdp, "core": load_core, "policy": load_policy,
    "bond": load_bond, "coffee": load_coffee, "trade": load_trade,
    "jobs": load_jobs, "fx": load_fx,
}


def validate(key, rows):
    lower, upper = LIMITS[key]
    dates = [date.fromisoformat(r["date"]) for r in rows]
    if not dates or len(set(dates)) != len(dates):
        raise ValueError("Empty series or duplicate dates")
    if any(d > datetime.now(timezone.utc).date() for d in dates):
        raise ValueError("Future observation date")
    if any(not number(r["value"]) or not lower <= r["value"] <= upper for r in rows):
        raise ValueError("Observation outside validation bounds")
    return sorted(rows, key=lambda r: r["date"])


def consecutive(first, values, note="", group="", publication=None):
    """Load bundled monthly or quarterly values. An underscore preserves a gap."""
    quarterly = "-Q" in first
    periods_per_year = 4 if quarterly else 12
    year = int(first[:4])
    first_period = int(first[-1]) if quarterly else int(first[5:])
    rows = []
    for offset, value in enumerate(values.split()):
        year_offset, index = divmod(first_period - 1 + offset, periods_per_year)
        period = (
            f"{year + year_offset}-Q{index + 1}" if quarterly
            else f"{year + year_offset}-{index + 1:02d}"
        )
        if value != "_":
            rows.append(point(period, float(value), note, group, publication))
    return rows


def bundled_state():
    # Publisher observations retained for cold starts and connection failures.
    # These text tables are rounded to nine significant digits where needed.
    gdp = consecutive("2015-Q1", """
        6.24543034 7.10849352 7.16211468 7.37863463 5.49308486 6.7444656
        6.91476806 7.49626141 5.17168615 6.6573907 7.53591699 8.18205326
        7.78253654 7.09782003 7.19576791 7.77926463 7.08508296 7.15847548
        7.62291725 7.52290434 3.21387235 0.336207469 2.9963616 4.6976674
        4.8451747 6.55416923 -6.03000236 5.22433333 5.41683028 8.56101038
        14.3842469 6.25757268 3.44626644 4.152713 5.22666246 6.75529763
        5.90525842 7.19421248 7.39359223 7.50586992 7.04967714 8.16010453
        8.25214165 8.45952141
    """, "2010 constant prices", "2010")
    gdp += consecutive(
        "2026-Q1", "8.14508469 8.81199596 9.9479885", "2020 constant prices", "2020"
    )

    core = consecutive("2015-04", """
        2.2 2.1 2.01 2.04 1.98 1.89 1.82 1.72 1.69 1.72 1.93 1.64
        1.76 1.87 1.88 1.85 1.83 1.85 1.86 1.87 1.87 1.88 1.51 1.6
        1.5 1.33 1.29 1.3 1.31 1.32 1.32 1.28 1.29 1.18 1.47 1.38
        1.33 1.37 1.37 1.41 1.54 1.61 1.67 1.72 1.7 1.83 1.82
    """, "NSO historical workbook", "historical")
    core += consecutive("2019-03", """
        1.84 1.88 1.9 1.96 2.04 1.95 1.96 1.99 2.18 2.78
        3.25 2.94 2.95 2.71 2.54 2.45 2.31
    """, "NSO monthly release", "historical")
    core += consecutive("2020-08", """
        2.15828672 1.96986486 1.87926764 1.61 0.99 0.491841804 0.793931954
        0.732057148 0.95087776 1.13 1.14 0.986927666 0.977033597 0.736629087
        0.5 0.58 0.67 0.660568242 0.676479845 1.09105024 1.46935635 1.61
        1.98 2.62896535 3.06383766 3.8233419 4.47 4.805018 4.99 5.20537015
        4.9573852 4.87999914 4.56 4.54 4.33 4.10972978 4.02101049 3.80366357
        3.42873335 3.14579374 2.98041544 2.7172426 2.95637581 2.76088401
        2.79411507 2.67525838 2.613387 2.61186483 2.53336643 2.53781406
        2.68 2.77 2.71 3.06946761 2.87189184 3.10146337 3.14172383
        3.32970978 3.45989989 3.30362531 3.24659901 3.17764957 3.298
    """, "Dataset base 2019", "Dataset base 2019")
    core += consecutive("2025-11", """
        3.2839534 3.26642909 3.19366847 3.7382662 3.96397337 4.66185531
        4.66781177 4.4957619 4.62664014 4.54800349 4.4546674 4.03633163
    """, "Dataset base 2024", "Dataset base 2024")

    policy = consecutive(
        "2015-01", "6.5 " * 30 + "6.25 " * 23, "Historical month-end", "archive"
    )
    decisions = [
        ("2019-09-16", 6.0, "1870", "2019-09-13"),
        ("2020-03-17", 5.0, "418", "2020-03-16"),
        ("2020-05-13", 4.5, "918", "2020-05-12"),
        ("2020-10-01", 4.0, "1728", "2020-09-30"),
        ("2022-09-23", 5.0, "1606", "2022-09-22"),
        ("2022-10-25", 6.0, "1809", "2022-10-24"),
        ("2023-04-03", 5.5, "574", "2023-03-31"),
        ("2023-05-25", 5.0, "950", "2023-05-24"),
        ("2023-06-19", 4.5, "1123", "2023-06-16"),
    ]
    policy.extend(
        point(day, rate, f"Effective date, Decision {code}", "decision", published)
        for day, rate, code, published in decisions
    )
    bond = [
        point("2025-12-31", 4.040, "ADB yield curve period-end reference"),
        point("2026-10-31", 4.465, "ADB yield curve period-end reference"),
        point("2026-11-18", 4.462, "Market close"),
    ]

    coffee = consecutive("2015-01", """
        2.16 2.17 2.03 2.03 1.93 1.99 1.92 1.89 1.8 1.82 1.8 1.75 1.65 1.63
        1.67 1.77 1.85 1.89 2 2.02 2.14 2.29 2.29 2.25 2.39 2.35 2.35 2.28
        2.17 2.25 2.31 2.3 2.19 2.17 2.01 1.93 1.95 1.97 1.94 1.95 1.96 1.9
        1.86 1.78 1.69 1.88 1.84 1.71 1.72 1.73 1.7 1.62 1.57 1.63 1.63
        1.56 1.56 1.51 1.62 1.61 1.56 1.5 1.49 1.41 1.42 1.42 1.49 1.6 1.6
        1.51 1.6 1.59 1.56 1.62 1.63 1.64 1.76 1.87 2.08 2.1 2.31 2.32
        2.41 2.48 2.43 2.41 2.29 2.29 2.27 2.29 2.21 2.42 2.46 2.27 2.04
        2.05 2.12 2.29 2.35 2.55 2.7 2.91 2.81 2.75 2.73 2.62 2.7 2.99
        3.26 3.38 3.66 4.23 4.04 4.5 4.73 4.73 5.33 4.89 4.98 5.22 5.41
        5.81 5.69 5.43 5.24 4.33 3.69 4.39 4.66 4.74 4.74 4.2 4.24 3.96
        3.9 3.63 3.67 3.73 4.07 3.98 3.69 3.63
    """, publication="2026-11-03")

    trade_table = """
2015 -0.644036612 -0.802814279 -1.2293231 0.413530565 -1.24395667 -0.047399368 -0.502103221 0.264559247 -0.013937281 0.439948369 0.1648613 -0.558415937
2016 0.816610807 -0.177941935 0.642586242 0.253176602 -0.231339703 -0.0903754785 0.418899178 0.532732984 0.741198735 -0.511358041 -0.32905701 -0.462690071
2017 1.23093985 -2.09502505 -1.29148027 0.156114751 -0.619890925 -0.348929441 0.241377337 1.50768006 0.950197246 2.09439286 0.500954517 -0.423052384
2018 0.098424894 0.337088975 2.24216252 0.997196412 -1.03995667 0.624385188 -0.646805661 2.23931382 1.64480963 0.833975423 0.251453079 -0.75220299
2019 0.815512155 -0.768207281 1.36322236 -0.554637928 -1.28921947 2.02242428 0.043221455 3.43486967 1.60800467 1.85986738 1.4537571 0.259176291
2020 -0.276387849 2.27524773 1.98031653 -0.939703025 1.01040981 1.85165158 2.77605907 4.98558388 2.95897484 2.93953169 0.545509312 -0.251740445
2021 2.08636681 -0.460190318 1.19715443 -1.22484765 -2.0738162 -0.454790713 -1.24713504 -0.109163608 0.360104179 2.73829109 1.3 2.96994592
2022 _ -1.96365661 2.04886596 0.849105853 -1.69808094 0.610616745 0.0743688 3.85888157 1.42947896 2.46659725 0.742438805 1.73668478
2023 _ _ _ _ _ _ _ _ _ 2.73376866 1.54323247 2.06095749
2024 3.62 1.38174291 2.77817404 1.06616549 -0.455822455 3.20109049 2.35577391 4.04965683 2.31916226 2.03390382 1.06652699 0.523982268
2025 _ _ _ _ _ _ _ _ _ 2.60400685 20.6898136 -0.636529086
2026 _ _ _ _ _ -2.64447529 -3.57278287 -0.113214838 1.29955495 7.30673726 _ _
    """
    trade = []
    for line in trade_table.strip().splitlines():
        year, values = line.split(maxsplit=1)
        trade.extend(consecutive(f"{year}-01", values, "Preliminary" if year >= "2018" else ""))

    jobs = consecutive("2016-Q1", """
        2.25 2.29 2.34 2.31 2.3 2.26 2.23 2.21 2.2 2.19 2.2 2.2 2.17 2.16
        2.17 2.15 2.22 2.73 2.5 2.37 2.42 2.6 3.98 3.56 2.46 2.32 2.28
        2.32 2.25 2.3 2.3 2.26 2.24403112 2.29017151 2.23441693 2.21575894
        2.2 2.24458 2.21235 2.21937 2.21 2.23189
    """)
    jobs.append(point("2026-Q3", 2.21782, "Preliminary"))

    fx = consecutive("2015-01", """
        21485 21485 21485 21485 21673 21673 21890 21890 21890 21890 21890 21890
        21881 21914 21857 21842 21939 21873 21862 21921 21949 22039 22118 22159
        22202 22232 22276 22350 22396 22431 22432 22443 22470 22471 22433 22425
        22441 22463 22458 22539 22595 22650 22669 22678 22714 22726 22750 22825
        22868 22915 22980 23028 23065 23066 23073 23133 23161 23145 23162 23155
        23196 23224 23235 23257 23261 23229 23213 23200 23215 23201 23155 23131
        23160 23130 23244 23158 23135 23178 23180 23130 23162 23131 23139 23145
        23099 23140 23100 23140 23057 23110 23176 23219 23400 23695 23679.35
        23612 23609 23644 23600 23639 23714 23758 23758 23997 24089 24087 23891
        23866 23991 24002 24003 24246 24261 24260 24255 24224 24093 24243 24251
        24335 24325 24726 24837 24956 24978 25052 25240 25240 25187 25093 25155
        25121 25074 25044 25102 25113 25139 25206 25338 25610
    """)

    sources = {
        "gdp": (gdp, "01:07:40"), "core": (core, "01:07:39"),
        "policy": (policy, "01:07:44"), "bond": (bond, "01:07:34"),
        "coffee": (coffee, "01:07:35"), "trade": (trade, "01:07:41"),
        "jobs": (jobs, "01:07:55"), "fx": (fx, "01:57:55"),
    }
    return {
        key: {
            "rows": validate(key, rows),
            "success": f"2026-11-20T{checked}+00:00",
            "attempt": f"2026-11-20T{checked}+00:00",
            "error": None,
            "bundled": True,
        }
        for key, (rows, checked) in sources.items()
    }


def merge_rows(key, *entries):
    return validate(key, list({r["date"]: r for rows in entries for r in rows}.values()))


def check(key, old):
    try:
        loaded = [r for r in LOADERS[key](META[key][3]) if r["date"] >= START.isoformat()]
        fresh = validate(key, loaded)
        previous = old.get("rows", [])
        if key != "policy" and previous and fresh[-1]["date"] < previous[-1]["date"]:
            return {**old, "attempt": stamp(), "error": "publisher returned an older observation"}
        if key in RETAIN_HISTORY:
            fresh = merge_rows(key, previous, fresh)
        now = stamp()
        return {"rows": fresh, "success": now, "attempt": now, "error": None}
    except Exception as exc:
        error = str(exc) if isinstance(exc.__cause__, requests.RequestException) else type(exc).__name__
        return {**old, "attempt": stamp(), "error": error}


def refresh(state):
    with ThreadPoolExecutor(max_workers=4) as workers:
        result = dict(zip(META, workers.map(lambda key: check(key, state.get(key, {})), META)))
    for key, entry in result.items():
        rows = entry.get("rows", [])
        latest = rows[-1]["period"] if rows else "-"
        outcome = f"ERROR {entry['error']}" if entry["error"] else (
            "OK limited archive" if key == "policy" else "OK"
        )
        print(
            f"{entry['attempt']} {key} [{PUBLISHERS[key]}] {outcome} "
            f"observations={len(rows)} latest={latest}", flush=True,
        )
    return result


def snapshot(text):
    raw = json.loads(text)
    if raw["version"] not in (1, 2) or not isinstance(raw["series"], dict):
        raise ValueError("Unexpected snapshot schema")
    imported = {}
    for key, entry in raw["series"].items():
        if key not in META:
            continue
        if not isinstance(entry, dict):
            raise ValueError("Invalid snapshot entry")
        for field in ("attempt", "success"):
            if entry.get(field) and datetime.fromisoformat(entry[field]).tzinfo is None:
                raise ValueError("Snapshot timestamp requires an offset")
        if entry.get("rows"):
            rows = entry["rows"]
            if raw["version"] == 1:
                rows = [{**r, "date": period_date(r["period"]).isoformat()} for r in rows]
            if any(r["date"] != period_date(r["period"]).isoformat() for r in rows):
                raise ValueError("Snapshot observation date and period differ")
            entry = {**entry, "rows": validate(key, rows)}
        imported[key] = entry
    return imported


def merge_snapshot(state, remote):
    combined = dict(state)
    for key, entry in remote.items():
        local = combined.get(key, {})
        newest = max((local, entry), key=lambda item: item.get("attempt", ""))
        valid = max((local, entry), key=lambda item: item.get("success", ""))
        if key in RETAIN_HISTORY and (local.get("rows") or entry.get("rows")):
            older = entry if valid is local else local
            valid = {**valid, "rows": merge_rows(key, older.get("rows", []), valid.get("rows", []))}
        combined[key] = {
            **valid, "attempt": newest.get("attempt", ""), "error": newest.get("error"),
        }
    return combined


def scheduled(path):
    previous = bundled_state()
    if path.exists():
        previous = merge_snapshot(previous, snapshot(path.read_text()))
    state = refresh(previous)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"version": 2, "series": state}, allow_nan=False))
    temporary.chmod(0o644)
    temporary.replace(path)
    return int(any(entry["error"] for entry in state.values()))


@st.cache_resource
def runtime():
    return {"lock": Lock(), "state": bundled_state(), "checked": None, "warning": ""}


def online(seconds, force):
    view = runtime()
    with view["lock"]:
        due = view["checked"] is None or monotonic() - view["checked"] >= seconds
        if not (force or due):
            return dict(view["state"]), view["warning"]

        state = view["state"]
        url = os.getenv("SNAPSHOT_URL")
        view["warning"] = ""
        remote = {}
        if url:
            try:
                if urlparse(url).scheme != "https":
                    raise ValueError("HTTPS snapshot required")
                remote = snapshot(fetch(url).text)
                state = merge_snapshot(state, remote)
            except (requests.RequestException, ValueError, KeyError, TypeError):
                view["warning"] = "Scheduled snapshot unavailable. Checking publishers from this app."

        cutoff = datetime.now(timezone.utc).timestamp() - seconds
        recent_schedule = not view["warning"] and all(
            remote.get(key, {}).get("attempt") and
            datetime.fromisoformat(remote[key]["attempt"]).timestamp() >= cutoff
            for key in META
        )
        view["state"] = refresh(state) if force or not recent_schedule else state
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
        origin = "bundled verified history" if entry.get("bundled") else "last validated data"
        st.warning(f"Publisher check failed ({entry['error']}). Showing {origin}.")
    if key == "policy":
        st.warning("Historical coverage only · current rate refresh unavailable")
    elif not entry.get("error"):
        st.caption("Bundled verified history" if entry.get("bundled") else "Source check succeeded")
    st.caption(f"Last success {entry.get('success', '—')} · Last attempt {entry.get('attempt', '—')}")
    note = f" · {latest['note']}" if latest.get("note") else ""
    st.caption(f"Publisher release / update {latest.get('publication') or 'unavailable in feed'}{note}")

    visible = [r for r in rows if first <= r["date"] <= last]
    if visible:
        previous = None
        segment = 0
        chart_rows = []
        for row in visible:
            observed = date.fromisoformat(row["date"])
            if previous and key in ("gdp", "jobs", "core", "trade", "coffee", "fx"):
                if (observed - previous).days > (110 if key in ("gdp", "jobs") else 45):
                    segment += 1
            chart_rows.append({**row, "line": f"{row['group']}/{segment}"})
            previous = observed

        base = alt.Chart(alt.Data(values=chart_rows)).encode(
            x=alt.X("date:T", title=None, scale=alt.Scale(type="utc", domain=[first, last])),
            y=alt.Y("value:Q", title=unit, scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("period:N", title="Observation"),
                alt.Tooltip("value:Q", title=unit, format=",.3f"),
                alt.Tooltip("note:N", title="Source note"),
            ],
        )
        marks = base.mark_point(filled=True, color="#087e74", size=36)
        if key != "bond":
            line = base.mark_line(
                color="#087e74", interpolate="step-after" if key == "policy" else "linear"
            )
            marks = line.encode(detail="line:N") + marks
        st.altair_chart(marks.properties(height=210), width="stretch")
    elif rows:
        st.info(f"No observations in this selection. Available coverage {rows[0]['period']} to {latest['period']}.")
    else:
        st.info("No validated observations available.")

    st.markdown(f"[Publisher source]({url}) · `{code}`")
    if key == "policy":
        st.markdown(f"[May decision]({MAY}) · [June decision]({JUNE})")
    if key == "core":
        st.markdown(f"[Historical NSO workbook]({OLD_CORE})")
    if key == "bond":
        st.markdown(f"[ADB yield curve and reference closes]({ADB_CURVE})")
    st.caption(definition)


def dashboard():
    st.set_page_config(page_title="Vietnam’s economy", layout="wide")
    st.title("Vietnam’s economy")
    seconds = max(10, int(os.getenv("REFRESH_SECONDS", "21600")))
    today = datetime.now(timezone.utc).date()
    dates = st.slider("Observation date range", START, today, (START, today), format="MMM YYYY")

    @st.fragment(run_every=min(seconds, 60))
    def panel():
        force = st.button("Refresh now", type="primary")
        state, warning = online(seconds, force)
        latest = max((entry.get("success", "") for entry in state.values()), default="")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", type=Path, metavar="SNAPSHOT")
    args, _ = parser.parse_known_args()
    if args.refresh is not None:
        raise SystemExit(scheduled(args.refresh))
    dashboard()
