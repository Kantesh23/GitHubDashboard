# GitHub PR Dashboard — Quick README snippet

## What this is
A Streamlit app that fetches GitHub pull request and contributor data and presents PR metrics, charts, tables and exportable reports (CSV and an Excel workbook with embedded charts).

## Quickstart
1. Clone the repo:
   git clone https://github.com/Kantesh23/GitHubDashboard.git
   cd GitHubDashboard

2. Install dependencies (example):
   pip install streamlit pandas numpy requests matplotlib xlsxwriter

3. (Optional) Set environment variables:
   - GITHUB_TOKEN — your GitHub personal access token (needed for private repos; include `repo` scope).
   - API_BASE — GitHub API base (defaults to https://api.github.com; set only for GitHub Enterprise).

   Example:
   export GITHUB_TOKEN="ghp_xxx"
   export API_BASE="https://api.github.com"

4. Run the app:
   streamlit run dashboards/github_metrics.py

## Using the app
- Enter or paste a GitHub token and API base in the sidebar (token is optional but avoids rate limits).
- Select owner and repo (or enter custom owner/repo).
- Choose date range, PR state (all/open/closed), whether to fetch PR reviews, and minimum changed-files filter.
- Click "Load data".
- The app displays KPI tiles, top authors, TAT histogram, merged-PR time series, slowest merged PRs, open PRs by age.
- Download options: CSV of PRs and a full Excel workbook (Summary + Charts + All PRs + Open PRs + Contributors, with embedded chart images).

## Notes & behavior
- The app uses caching (st.cache_data) to reduce API calls (repo list cached ~10 min; PRs/contributors cached ~30 min).
- It handles pagination and basic rate-limit responses; per-PR detail calls have a small delay to reduce secondary rate limits.
- Date/time fields are handled as UTC-aware datetimes; exported Excel datetimes are made Excel-friendly.
- For private repos, provide a token with appropriate scopes to avoid 401/403.

## File summary: dashboards/github_metrics.py
- Config / auth
  - Reads token/API base from env or sidebar, builds HEADERS for requests, clears cache on change.

- Utilities
  - sanitize_df_for_excel(df): convert timezone-aware datetimes to naive UTC for Excel.
  - fig_to_png_bytes(fig): serialize matplotlib figures to PNG bytes.
  - iso_to_utc_ts(s): parse ISO timestamp to tz-aware UTC.

- Plot builders (matplotlib)
  - build_sparkline_fig(dates), build_hist_fig(series), build_timeseries_fig(dates) — used for in-app charts and Excel export.

- Excel export
  - export_to_excel_with_charts(...): generate an in-memory XLSX with Summary, Charts, All PRs, Open PRs, Contributors; inserts PNG charts using xlsxwriter.
  - export_to_excel(...): wrapper for backward compatibility.

- HTTP helpers & pagination
  - gh_get(url, params): GET with auth, handles 401 and rate-limit 403.
  - paginate(url, params): fetch paginated GitHub REST endpoints (per_page=100).

- Data fetchers (cached)
  - fetch_repos_for_owner(owner): list repos for user/org.
  - fetch_prs(owner, repo, since, until, state): fetch PRs, normalize fields, parse datetimes, and fetch per-PR detail stats (additions, deletions, changed_files, commits).
  - fetch_pr_reviews(owner, repo, pr_number): fetch PR reviews (paginated).
  - fetch_contributors(owner, repo): contributors list normalized to user/commits.

- Metrics & plotting helpers
  - compute_pr_metrics(df_prs, owner, repo, sample_reviews=True): compute tat_days, age_days, optionally first_review_hours and review_count by sampling reviews.
  - plot_hist / plot_timeseries: small wrappers to show charts in Streamlit UI.

- Streamlit app
  - run_app(): builds the UI (sidebar inputs), triggers data fetch, computes metrics, renders KPIs/charts/tables and download buttons.
