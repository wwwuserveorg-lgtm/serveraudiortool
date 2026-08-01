# UServe Contractor Service Review Console

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Create a private GitHub repository and upload `app.py`, `requirements.txt`, and this README.
2. In Streamlit Community Cloud, choose **Create app**, select that repository, and set the main file to `app.py`.
3. Use the default Python version and deploy.

## Important production note

This starter uses SQLite (`userve_audits.db`) so it will work immediately on a local computer. Streamlit Community Cloud storage is not durable: data can be lost whenever the app restarts or is redeployed. Before using it for real audit history, replace SQLite with a hosted Postgres database (Supabase, Neon, or similar) and store the connection string as a Streamlit secret. Do not store passwords, API keys, or database connection strings in this repository.

## Branding

- The app uses a hunter-green color theme (`.streamlit/config.toml`) with matching tier
  badge colors and a branded header on every page.
- To show your logo: drop a PNG (or JPG) file at `assets/logo.png` in this project. It
  will automatically appear in the sidebar and on every page header. If no file is
  present, a plain "USERVE" wordmark placeholder is shown instead — nothing breaks, it
  just won't be your real logo until you add the file. This avoids hotlinking to an
  external site's logo, which can break if that site changes or blocks automated access.

## Included workflow

- Add active/inactive contractors with territory and weekly audit day/time.
- From the Service Audit screen, generate a one-time, 15-minute access code so a contractor
  can log in (via the sidebar "Contractor Login" page) and see a read-only version of the
  auditor's screen for their own record while the audit is in progress — score, coverage
  chart, warnings, urgent jobs, and the auditor's note. Generating a new code invalidates
  any unused code for that contractor, and a code is consumed the instant it's used, so a
  contractor session can't be shared or replayed.
- Upload one or more 90-day CSV reports for a single contractor.
- Parse dated attempt records from the `Attempts` column, including `<br />` separators.
- Score completion rate, first-attempt speed, five-attempt non-serve coverage, speed to service, and time-window coverage.
- Exclude open jobs from completed-service calculations.
- Treat five attempts as required only for Unable Contact, Avoiding, or Time returns, unless the assignment was given fewer than five days before court.
- Show urgent court dates within ten days.
- Archive a review, automatically clear it from the current weekly queue, manage scheduled audits, and delete an incorrect saved review.
