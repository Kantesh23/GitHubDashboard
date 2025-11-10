# File: dashboards/github_metrics.py
"""
Streamlit dashboard: GitHub PR & contributor metrics
Run:
  export GITHUB_TOKEN="ghp_xxx"
  streamlit run dashboards/github_metrics.py
"""

from datetime import datetime, timedelta, timezone
import os
import time
from typing import List, Optional

import requests
import pandas as pd
import numpy as np
import streamlit as st
import matplotlib.pyplot as plt
from collections import Counter

# ---------------------------
# Config / auth
# ---------------------------
GITHUB_TOKEN = ""#os.environ.get("GITHUB_TOKEN")
API_BASE = ""
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

from io import BytesIO
import math
import matplotlib.dates as mdates

def sanitize_df_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Make datetime columns Excel-friendly (naive UTC)."""
    if df is None:
        return pd.DataFrame()
    out = df.copy()
    for col in out.columns:
        try:
            if pd.api.types.is_datetime64_any_dtype(out[col]):
                if pd.api.types.is_datetime64tz_dtype(out[col]):
                    out[col] = out[col].dt.tz_convert("UTC").dt.tz_localize(None)
                else:
                    out[col] = pd.to_datetime(out[col], errors="coerce")
        except Exception:
            continue
    return out

def fig_to_png_bytes(fig, dpi=120):
    """Serialize matplotlib figure to PNG bytes and close figure."""
    bio = BytesIO()
    fig.savefig(bio, format="png", bbox_inches="tight", dpi=dpi)
    plt.close(fig)
    bio.seek(0)
    return bio

def build_sparkline_fig(dates: pd.Series, freq="W", pts=12, figsize=(3.0, 0.6)):
    """Return a small sparkline matplotlib Figure or None."""
    if dates is None or len(dates) == 0:
        return None
    dt = pd.to_datetime(dates)
    # drop tz for plotting
    try:
        if getattr(dt.dt, "tz", None) is not None and dt.dt.tz is not None:
            dt = dt.dt.tz_convert("UTC").dt.tz_localize(None)
    except Exception:
        try:
            dt = dt.dt.tz_localize(None)
        except Exception:
            pass
    idx = pd.DatetimeIndex(dt.values)
    if len(idx) == 0:
        return None
    counts = pd.Series(1, index=idx).resample(freq).sum().sort_index()
    counts = counts[-pts:]
    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(counts.index, counts.values, linewidth=1)
    ax.fill_between(counts.index, counts.values, alpha=0.15)
    ax.axis("off")
    return fig

def build_hist_fig(series: pd.Series, bins=30, figsize=(6, 3)):
    """Histogram for TAT distribution."""
    if series is None or len(series.dropna()) == 0:
        return None
    fig, ax = plt.subplots(figsize=figsize)
    ax.hist(series.dropna(), bins=bins)
    ax.set_xlabel("Days")
    ax.set_ylabel("Count")
    ax.set_title("TAT distribution (days)")
    fig.tight_layout()
    return fig

def build_timeseries_fig(dates: pd.Series, freq="W", figsize=(8, 3)):
    """Larger time series (counts per period)."""
    if dates is None or len(dates) == 0:
        return None
    dt = pd.to_datetime(dates)
    try:
        if getattr(dt.dt, "tz", None) is not None and dt.dt.tz is not None:
            dt = dt.dt.tz_convert("UTC").dt.tz_localize(None)
    except Exception:
        try:
            dt = dt.dt.tz_localize(None)
        except Exception:
            pass
    idx = pd.DatetimeIndex(dt.values)
    if len(idx) == 0:
        return None
    counts = pd.Series(1, index=idx).resample(freq).sum().sort_index()
    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(counts.index, counts.values, marker="o")
    ax.set_title("Merged PRs over time")
    ax.set_ylabel("Count")
    ax.set_xlabel("Period")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig

def export_to_excel_with_charts(owner: str,
                                repo: str,
                                df_metrics: pd.DataFrame,
                                open_prs_df: pd.DataFrame,
                                contributors: pd.DataFrame,
                                start_date,
                                end_date) -> BytesIO:
    """
    Export dashboard data AND charts into an Excel workbook (in-memory BytesIO).
    Produces sheets: Summary (KPIs + charts), All PRs, Open PRs, Contributors.
    """
    # sanitize frames
    df_all = sanitize_df_for_excel(df_metrics.copy()) if df_metrics is not None else pd.DataFrame()
    df_open = sanitize_df_for_excel(open_prs_df.copy()) if open_prs_df is not None else pd.DataFrame()
    df_contrib = sanitize_df_for_excel(contributors.copy()) if contributors is not None else pd.DataFrame()

    # KPIs
    total = len(df_all)
    merged = int(df_all["merged_at"].notna().sum()) if "merged_at" in df_all.columns else 0
    open_count = int((df_all["state"] == "open").sum()) if "state" in df_all.columns else 0
    merge_rate = (merged / total * 100) if total else 0.0
    tat_series = df_all["tat_days"].dropna() if "tat_days" in df_all.columns else pd.Series(dtype=float)
    avg_tat = float(tat_series.mean()) if not tat_series.empty else float("nan")
    active_authors = len(set(df_all["author"].dropna().tolist())) if "author" in df_all.columns else 0

    # Build chart figures (sparkline & larger)
    created_dates = df_all["created_at"].dropna() if "created_at" in df_all.columns else pd.Series(dtype='datetime64[ns]')
    merged_dates = df_all[df_all["merged_at"].notna()]["merged_at"] if "merged_at" in df_all.columns else pd.Series(dtype='datetime64[ns]')
    # weekly_avg_tat
    weekly_avg_tat = None
    try:
        tat_rows = df_all[df_all["tat_days"].notna()].copy()
        tat_rows = tat_rows[tat_rows["merged_at"].notna()]
        if not tat_rows.empty:
            merged_idx = tat_rows["merged_at"].dt.tz_convert("UTC").dt.tz_localize(None) if pd.api.types.is_datetime64tz_dtype(tat_rows["merged_at"]) else tat_rows["merged_at"]
            tat_rows = tat_rows.set_index(merged_idx)
            weekly_avg_tat = tat_rows["tat_days"].resample("W").mean().dropna()
    except Exception:
        weekly_avg_tat = None

    # small sparklines
    spark_created = build_sparkline_fig(created_dates, freq="W", pts=12)
    spark_merged = build_sparkline_fig(merged_dates, freq="W", pts=12)
    spark_tat = None
    if weekly_avg_tat is not None and len(weekly_avg_tat) > 0:
        # weekly_avg_tat index may be DatetimeIndex already
        fig, ax = plt.subplots(figsize=(3.0, 0.6))
        ax.plot(weekly_avg_tat.index, weekly_avg_tat.values, linewidth=1)
        ax.axis("off")
        spark_tat = fig

    # big charts
    hist_fig = build_hist_fig(tat_series, bins=30)
    timeseries_fig = build_timeseries_fig(merged_dates, freq="W")

    # convert figs to bytes
    images = {}
    if spark_created: images["spark_created"] = fig_to_png_bytes(spark_created)
    if spark_merged: images["spark_merged"] = fig_to_png_bytes(spark_merged)
    if spark_tat: images["spark_tat"] = fig_to_png_bytes(spark_tat)
    if hist_fig: images["hist"] = fig_to_png_bytes(hist_fig, dpi=150)
    if timeseries_fig: images["timeseries"] = fig_to_png_bytes(timeseries_fig, dpi=150)

    # Build Excel workbook
    out = BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm:ss") as writer:
        workbook = writer.book

        # 1) Summary sheet: KPI table
        summary_rows = [
            ["Owner", owner],
            ["Repository", repo],
            ["Date Range", f"{start_date} → {end_date}"],
            [],
            ["Metric", "Value"],
            ["PRs (Fetched)", total],
            ["Merged", merged],
            ["Open PRs", open_count],
            ["Merge Rate (%)", round(merge_rate, 1)],
            ["Avg TAT (days)", (round(avg_tat, 2) if not math.isnan(avg_tat) else "n/a")],
            ["Active PR Authors", active_authors],
        ]
        df_summary = pd.DataFrame(summary_rows)
        df_summary.to_excel(writer, sheet_name="Summary", index=False, header=False)
        ws = writer.sheets["Summary"]

        # Insert small sparklines next to KPI values
        # locate row numbers: PRs at row 6 (1-based in display), but Excel uses 0-based index
        # We'll insert images near column D for the small sparkline visuals
        img_col = 3  # column D (0-based)
        start_row = 5  # pandas wrote first row at Excel row 1; our KPI table starts around row 6 (0-based 5)
        if "spark_created" in images:
            ws.insert_image(start_row, img_col, "spark_created.png", {'image_data': images["spark_created"], 'x_offset': 2, 'y_offset': 2, 'x_scale': 0.9, 'y_scale': 0.9})
        if "spark_merged" in images:
            ws.insert_image(start_row + 1, img_col, "spark_merged.png", {'image_data': images["spark_merged"], 'x_offset': 2, 'y_offset': 2, 'x_scale': 0.9, 'y_scale': 0.9})
        if "spark_tat" in images:
            ws.insert_image(start_row + 4, img_col, "spark_tat.png", {'image_data': images["spark_tat"], 'x_offset': 2, 'y_offset': 2, 'x_scale': 0.9, 'y_scale': 0.9})

        # Insert bigger charts into a "Charts" area in the Summary sheet (or a separate Charts sheet)
        # We'll create a Charts sheet for better layout
        ws_charts = workbook.add_worksheet("Charts")
        writer.sheets["Charts"] = ws_charts
        # place histogram and timeseries
        row_offset = 1
        col_offset = 1
        if "hist" in images:
            ws_charts.insert_image(row_offset, col_offset, "hist.png", {'image_data': images["hist"], 'x_scale': 1.0, 'y_scale': 1.0})
        if "timeseries" in images:
            # place timeseries to the right of histogram
            ws_charts.insert_image(row_offset, col_offset + 8, "timeseries.png", {'image_data': images["timeseries"], 'x_scale': 1.0, 'y_scale': 1.0})

        # All PRs sheet
        if not df_all.empty:
            df_all.to_excel(writer, sheet_name="All PRs", index=False)
            ws_all = writer.sheets["All PRs"]
            # autofit columns (simple heuristic)
            for i, col in enumerate(df_all.columns):
                max_len = max(df_all[col].astype(str).map(len).max(), len(str(col))) + 2
                max_len = min(max_len, 100)
                ws_all.set_column(i, i, max_len)

        # Open PRs sheet
        if not df_open.empty:
            df_open.to_excel(writer, sheet_name="Open PRs", index=False)
            ws_open = writer.sheets["Open PRs"]
            for i, col in enumerate(df_open.columns):
                max_len = max(df_open[col].astype(str).map(len).max(), len(str(col))) + 2
                max_len = min(max_len, 100)
                ws_open.set_column(i, i, max_len)

        # Contributors sheet
        if not df_contrib.empty:
            df_contrib.to_excel(writer, sheet_name="Contributors", index=False)
            ws_contrib = writer.sheets["Contributors"]
            for i, col in enumerate(df_contrib.columns):
                max_len = max(df_contrib[col].astype(str).map(len).max(), len(str(col))) + 2
                max_len = min(max_len, 100)
                ws_contrib.set_column(i, i, max_len)

        # format Summary column widths
        try:
            ws.set_column(0, 0, 20)  # names
            ws.set_column(1, 1, 30)  # values
            ws.set_column(3, 3, 24)  # sparkline image column
        except Exception:
            pass

    out.seek(0)
    return out

def export_to_excel(owner: str,
                    repo: str,
                    df_metrics: pd.DataFrame,
                    open_prs_df: pd.DataFrame,
                    contributors: pd.DataFrame,
                    start_date,
                    end_date):
    """
    Backwards-compatible wrapper.
    Calls export_to_excel_with_charts() so older call sites don't break.
    """
    # If you prefer a simpler export (no charts), replace call below with your simpler exporter.
    return export_to_excel_with_charts(owner, repo, df_metrics, open_prs_df, contributors, start_date, end_date)

# ---------------------------
# HTTP helpers & pagination
# ---------------------------
def gh_get(url: str, params: dict = None) -> requests.Response:
    r = requests.get(url, headers=HEADERS, params=params or {})
    if r.status_code == 401:
        st.error("Invalid or missing GITHUB_TOKEN. Set env var GITHUB_TOKEN.")
        st.stop()
    if r.status_code == 403 and "rate limit" in r.text.lower():
        reset = r.headers.get("X-RateLimit-Reset")
        if reset:
            wait = int(reset) - int(time.time())
            raise RuntimeError(f"Rate limited. Retry after {wait} seconds.")
    r.raise_for_status()
    return r

def paginate(url: str, params: dict = None) -> List[dict]:
    items = []
    params = dict(params or {})
    params["per_page"] = 100
    page = 1
    while True:
        params["page"] = page
        r = gh_get(url, params=params)
        page_items = r.json()
        if not isinstance(page_items, list):
            break
        items.extend(page_items)
        if "Link" in r.headers:
            if 'rel="next"' not in r.headers["Link"]:
                break
        else:
            if len(page_items) < 100:
                break
        page += 1
    return items

# ---------------------------
# Repo listing (cached)
# ---------------------------
@st.cache_data(ttl=60 * 10)  # cache for 10 minutes
def fetch_repos_for_owner(owner: str) -> List[str]:
    """
    Try both user and org endpoints; return list of repo names sorted by name.
    """
    if not owner:
        return []
    urls = [
        f"{API_BASE}/users/{owner}/repos",
        f"{API_BASE}/orgs/{owner}/repos",
    ]
    for url in urls:
        try:
            items = paginate(url, params={"type": "all", "sort": "updated"})
            if items:
                names = sorted([it["name"] for it in items])
                return names
        except requests.HTTPError:
            # try next URL
            continue
    return []

# ---------------------------
# PR & contributors fetchers
# ---------------------------
def iso_to_utc_ts(s: Optional[str]):
    if s is None:
        return None
    ts = pd.to_datetime(s)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")

@st.cache_data(ttl=60 * 30)
def fetch_prs(owner: str, repo: str, since: Optional[str], until: Optional[str], state: str = "all") -> pd.DataFrame:
    """
    Fetch PR metadata + detailed stats (additions, deletions, changed_files)
    between `since` and `until` dates. Caches for 30 minutes.
    """
    params = {"state": state, "sort": "created", "direction": "asc"}
    url = f"{API_BASE}/repos/{owner}/{repo}/pulls"
    prs = paginate(url, params=params)
    df = pd.json_normalize(prs)
    if df.empty:
        return df

    keep = [
        "number", "title", "user.login", "state", "created_at", "updated_at",
        "closed_at", "merged_at", "draft", "html_url"
    ]
    for c in keep:
        if c not in df.columns:
            df[c] = None
    df = df[keep].rename(columns={"user.login": "author", "html_url": "url"})

    # parse datetimes as tz-aware UTC
    for col in ["created_at", "updated_at", "closed_at", "merged_at"]:
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    # filter by date range
    if since:
        since_dt = iso_to_utc_ts(since)
        df = df[df["created_at"] >= since_dt]
    if until:
        until_dt = iso_to_utc_ts(until) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        df = df[df["created_at"] <= until_dt]

    df = df.reset_index(drop=True)

    # --- Fetch per-PR detail stats ---
    additions, deletions, changed_files, commits = [], [], [], []

    # st.write(f"Fetching detailed stats for {len(df)} PRs...")
    with st.spinner(f"Fetching detailed stats for {len(df)} PRs..."):
        for _, row in df.iterrows():
            pr_number = int(row["number"])
            detail_url = f"{API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
            try:
                r = gh_get(detail_url)
                detail = r.json()
                additions.append(detail.get("additions", 0))
                deletions.append(detail.get("deletions", 0))
                changed_files.append(detail.get("changed_files", 0))
                commits.append(detail.get("commits", 0))
                # short delay to avoid secondary rate limit
                time.sleep(0.2)
            except Exception as e:
                additions.append(np.nan)
                deletions.append(np.nan)
                changed_files.append(np.nan)
                commits.append(np.nan)

        df["additions"] = additions
        df["deletions"] = deletions
        df["changed_files"] = changed_files
        df["commits"] = commits

    return df


def fetch_pr_reviews(owner: str, repo: str, pr_number: int) -> List[dict]:
    url = f"{API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    return paginate(url)



@st.cache_data(ttl=60 * 30)
def fetch_contributors_by_date(owner: str, repo: str, since: Optional[str], until: Optional[str], max_commits: int = 5000) -> pd.DataFrame:
    """
    Return contributor commit counts restricted to the date range [since, until].
    - Uses the commits REST endpoint: /repos/{owner}/{repo}/commits?since=...&until=...
    - Aggregates by commit author login (falls back to commit.author.name/email when login missing).
    - max_commits: safety cap to avoid enumerating huge histories (increase if you know size).
    Returns DataFrame with columns: user, commits (int).
    """
    if not owner or not repo:
        return pd.DataFrame(columns=["user", "commits"])

    params = {}
    if since:
        params["since"] = pd.to_datetime(since).isoformat()
    if until:
        # include full 'until' day by adding one day - 1 microsecond
        until_dt = pd.to_datetime(until)
        params["until"] = (until_dt + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)).isoformat()

    base = f"{API_BASE}/repos/{owner}/{repo}/commits"
    per_page = 100
    page = 1
    seen = 0
    counts = Counter()

    while True:
        params_page = dict(params)
        params_page["per_page"] = per_page
        params_page["page"] = page
        try:
            r = gh_get(base, params=params_page)
            commits = r.json()
        except Exception as e:
            st.warning(f"Error fetching commits page {page}: {e}")
            break

        if not commits:
            break

        for c in commits:
            # prefer author.login when available (GitHub account)
            author = None
            if c.get("author") and isinstance(c["author"], dict):
                author = c["author"].get("login")
            # fallback to commit author name/email (non-GitHub authors)
            if not author:
                commit_info = c.get("commit", {}).get("author", {}) or {}
                # use email if available (safer unique), else name
                author = commit_info.get("email") or commit_info.get("name") or "unknown"
            counts[author] += 1
            seen += 1
            if seen >= max_commits:
                break

        # break conditions
        if seen >= max_commits:
            st.info(f"Reached max_commits cap ({max_commits}) while fetching commits.")
            break

        # pagination: if fewer than per_page returned, done
        if len(commits) < per_page:
            break
        page += 1

    # build DataFrame sorted desc by commits
    rows = [{"user": user, "commits": cnt} for user, cnt in counts.most_common()]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df

@st.cache_data(ttl=60 * 30)
def fetch_contributors(owner: str, repo: str) -> pd.DataFrame:
    url = f"{API_BASE}/repos/{owner}/{repo}/contributors"
    rows = paginate(url)
    df = pd.json_normalize(rows)
    if df.empty:
        return df
    df = df[["login", "contributions"]].rename(columns={"login": "user", "contributions": "commits"})
    return df

# ---------------------------
# Metrics & plotting
# ---------------------------
def compute_pr_metrics(df_prs: pd.DataFrame, owner: str, repo: str, sample_reviews: bool = True) -> pd.DataFrame:
    if df_prs.empty:
        return df_prs
    df = df_prs.copy()
    df["tat_days"] = (df["merged_at"] - df["created_at"]).dt.total_seconds() / 86400.0
    now = pd.to_datetime(datetime.now(timezone.utc))
    df["age_days"] = ((df["updated_at"].fillna(now)) - df["created_at"]).dt.total_seconds() / 86400.0
    df["first_review_hours"] = np.nan
    df["review_count"] = 0
    if sample_reviews:
        for idx, row in df.iterrows():
            try:
                reviews = fetch_pr_reviews(owner, repo, int(row["number"]))
                if reviews:
                    rev_df = pd.json_normalize(reviews)
                    rev_df["submitted_at"] = pd.to_datetime(rev_df["submitted_at"], utc=True)
                    rev_df = rev_df.sort_values("submitted_at")
                    first = rev_df.iloc[0]["submitted_at"]
                    df.at[idx, "first_review_hours"] = (first - row["created_at"]).total_seconds() / 3600.0
                    df.at[idx, "review_count"] = len(rev_df)
                else:
                    df.at[idx, "review_count"] = 0
            except Exception:
                df.at[idx, "review_count"] = np.nan
    return df

def plot_hist(series: pd.Series, xlabel: str, title: str):
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.hist(series.dropna(), bins=30)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("count")
    ax.set_title(title)
    st.pyplot(fig)

def plot_timeseries(dates: pd.Series, title: str, freq="W"):
    if dates is None or len(dates) == 0:
        st.info("No dates to plot.")
        return
    dt = pd.to_datetime(dates)
    try:
        if getattr(dt.dt, "tz", None) is not None and dt.dt.tz is not None:
            dt = dt.dt.tz_convert("UTC").dt.tz_localize(None)
    except Exception:
        try:
            dt = dt.dt.tz_localize(None)
        except Exception:
            pass
    idx = pd.DatetimeIndex(dt.values)
    if len(idx) == 0:
        st.info("No dates to plot.")
        return
    counts = pd.Series(1, index=idx).resample(freq).sum().sort_index()
    if len(counts) > 0:
        full_index = pd.date_range(start=counts.index.min(), end=counts.index.max(), freq=freq)
        counts = counts.reindex(full_index, fill_value=0)
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.plot(counts.index, counts.values, marker="o")
    ax.set_title(title)
    ax.set_ylabel("count")
    ax.set_xlabel("period")
    plt.xticks(rotation=45)
    st.pyplot(fig)

# ---------------------------
# Streamlit app
# ---------------------------
def run_app():
    st.set_page_config(page_title="GitHub PR Dashboard", layout="wide")
    st.title("GitHub PR & Contributor Dashboard")

        # --- UI for token / api base (allow end-user to provide these securely) ---
    st.sidebar.header("API / Authentication")

    # Pre-fill from env (if any) but allow override. Use password mask for token.
    token_input = st.sidebar.text_input(
        "GitHub token (personal access token)", 
        value=os.environ.get("GITHUB_TOKEN", ""), 
        type="password",
        help="Enter a GitHub personal access token. For private repos include 'repo' scope. Leave empty for unauthenticated (rate-limited) access."
    )
    api_base_input = st.sidebar.text_input(
        "GitHub API base URL",
        value=os.environ.get("API_BASE", "https://api.github.com/"),
        help="Usually https://api.github.com/. Change only if using a GitHub Enterprise server."
    )

    # Normalize inputs
    api_base_input = api_base_input.strip().rstrip("/") if api_base_input else "https://api.github.com"

    # Apply to module-global variables and clear cache if they changed
    global GITHUB_TOKEN, API_BASE, HEADERS
    changed = False
    if token_input != (GITHUB_TOKEN or ""):
        GITHUB_TOKEN = token_input
        changed = True
    if api_base_input != (API_BASE or ""):
        API_BASE = api_base_input
        changed = True

    # Build headers for API calls; use "token" auth for REST endpoints.
    if GITHUB_TOKEN:
        HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"}
    else:
        HEADERS = {}

    # If auth / API base changed, clear cached API results so we don't mix responses
    if changed:
        try:
            st.cache_data.clear()  # clear cached API responses that may depend on token/base
        except Exception:
            pass

    # Security note in UI
    if GITHUB_TOKEN:
        st.sidebar.success("Using provided GitHub token (kept private during this session).")
    else:
        st.sidebar.warning("No token provided — API calls will be unauthenticated and rate-limited.")


    # Sidebar: prefilled owners + custom override
    st.sidebar.header("Repository Selection")
    preset_owners = ["Sales-Comp-IT"]
    owner_choice = st.sidebar.selectbox("Choose owner (prefilled)", options=[""] + preset_owners, index=0)
    custom_owner = st.sidebar.text_input("Or enter custom owner (overrides above)", value="")
    owner = custom_owner.strip() or owner_choice.strip()

    # Fetch repos for selected owner
    repos = []
    if owner:
        try:
            repos = fetch_repos_for_owner(owner)
        except Exception as e:
            st.sidebar.error(f"Unable to fetch repos for '{owner}': {e}")
            repos = []

    repo = None
    if repos:
        repo = st.sidebar.selectbox("Repository", options=repos)
    else:
        repo = st.sidebar.text_input("Repository (repo name)", value="")

    st.sidebar.write("Date range (PR created_at)")
    col1, col2 = st.sidebar.columns(2)
    since = col1.date_input("Since", value=(datetime.utcnow() - timedelta(days=90)).date())
    until = col2.date_input("Until", value=datetime.utcnow().date())
    state = st.sidebar.selectbox("PR state to fetch", ["all", "open", "closed"], index=0)
    sample_reviews = st.sidebar.checkbox("Fetch PR reviews (slower)", value=True)
    min_pr_size = st.sidebar.number_input("Min changed files (filter)", value=0, min_value=0)

    if st.sidebar.button("Load data"):
        if not owner or not repo:
            st.sidebar.error("Please provide both owner and repository name.")
            st.stop()
        with st.spinner("Fetching PRs..."):
            df_prs = fetch_prs(owner, repo, since.isoformat(), until.isoformat(), state=state)
            if df_prs.empty:
                st.warning("No PRs found for this repo/time range.")
                st.stop()

            # contributors = fetch_contributors(owner, repo)
            contributors = fetch_contributors_by_date(owner, repo, since.isoformat(), until.isoformat(), max_commits=2000)
            df_metrics = compute_pr_metrics(df_prs, owner, repo, sample_reviews=sample_reviews)
            if "changed_files" in df_metrics.columns:
                df_metrics = df_metrics[df_metrics["changed_files"].fillna(0) >= int(min_pr_size)]

            # compute KPI values
            total = len(df_metrics)
            merged = int(df_metrics["merged_at"].notna().sum())
            open_prs = int((df_metrics["state"] == "open").sum())
            merge_rate = (merged / total * 100) if total else 0.0
            tat_series = df_metrics["tat_days"].dropna()
            avg_tat = float(tat_series.mean()) if not tat_series.empty else float("nan")
            median_tat = float(tat_series.median()) if not tat_series.empty else float("nan")
            p95_tat = float(tat_series.quantile(0.95)) if not tat_series.empty else float("nan")
            top_contributors = df_metrics["author"].value_counts().head(5).to_dict()
            active_contrib_count = len(set(df_metrics["author"].dropna().tolist()))

            # Top KPI tiles
            kcols = st.columns(5)
            kcols[0].metric("PRs (fetched)", total)
            kcols[1].metric("Merged", f"{merged} ({merge_rate:.1f}%)")
            kcols[2].metric("Open PRs", open_prs)
            kcols[3].metric("Avg TAT (days)", f"{avg_tat:.1f}" if not np.isnan(avg_tat) else "n/a")
            #kcols[4].metric("Median TAT (days)", f"{median_tat:.1f}" if not np.isnan(median_tat) else "n/a")
            kcols[4].metric("Active PR Authors", active_contrib_count)

            # compact cards row (small details) — note: 95th tile removed
            card_cols = st.columns(2)

            with card_cols[0]:
                st.markdown("#### Top 5 PR Authors")
                if top_contributors:
                    for name, count in top_contributors.items():
                        st.markdown(f"- **{name}** — {count}")
                else:
                    st.markdown("No authors")

            with card_cols[1]:
                st.markdown("#### Contributors (commits)")
                if not contributors.empty:
                    topc = contributors.sort_values("commits", ascending=False).head(5)
                    for _, r in topc.iterrows():
                        st.markdown(f"- **{r['user']}** — {int(r['commits'])}")
                else:
                    st.markdown("No commit data")

            # Charts and tables
            st.markdown("---")

            # NEW: display the two charts side-by-side in a single row with two tiles
            chart_col1, chart_col2 = st.columns(2, gap="large")

            with chart_col1:
                st.subheader("TAT distribution (merged PRs)")
                plot_hist(tat_series, xlabel="Days", title="TAT (days) for merged PRs")

            with chart_col2:
                st.subheader("Merged PRs over time")
                merged_dates = df_metrics[df_metrics["merged_at"].notna()]["merged_at"]
                plot_timeseries(merged_dates, title="Merged PRs per week", freq="W")

            st.subheader("Slowest merged PRs")
            slow = df_metrics[df_metrics["tat_days"].notna()].sort_values("tat_days", ascending=False).head(10)
            #show_cols = ["number", "title", "author", "created_at", "merged_at", "tat_days", "changed_files", "additions", "deletions", "url"]
            show_cols = ["title", "author", "created_at", "merged_at", "tat_days", "changed_files", "additions", "deletions", "url"]
            slow_display = slow[show_cols].copy()
            slow_display["created_at"] = slow_display["created_at"].dt.strftime("%Y-%m-%d")
            slow_display["merged_at"] = slow_display["merged_at"].dt.strftime("%Y-%m-%d")
            st.dataframe(slow_display)

            st.subheader("Open PRs (age)")
            open_prs_df = df_metrics[df_metrics["state"] == "open"].sort_values("age_days", ascending=False).head(20)
            if not open_prs_df.empty:
                #open_disp = open_prs_df[["number", "title", "author", "created_at", "age_days", "changed_files", "url"]].copy()
                open_disp = open_prs_df[["title", "author", "created_at", "age_days", "changed_files", "url"]].copy()
                open_disp["created_at"] = open_disp["created_at"].dt.strftime("%Y-%m-%d")
                st.dataframe(open_disp)
            else:
                st.info("No open PRs in selection.")

            # CSV export
            csv = df_metrics.to_csv(index=False)
            st.download_button("Download PR data (CSV)", data=csv, file_name=f"{owner}_{repo}_prs.csv")

            # Export full dashboard data to Excel
            excel_data = export_to_excel(owner, repo, df_metrics, open_prs_df, contributors, since, until)
            st.download_button(
                label="📊 Download full dashboard (Excel)",
                data=excel_data,
                file_name=f"{owner}_{repo}_dashboard.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            st.success("Done.")

if __name__ == "__main__":
    run_app()
