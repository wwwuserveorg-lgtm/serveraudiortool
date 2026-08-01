# UServe Contractor Service Review Console

## Database setup (required — do this first)

This app stores all data in a hosted Postgres database — there is no local file
database and no fallback, so you need a connection string before it will run.

1. Create a free Postgres database with [Supabase](https://supabase.com) or
   [Neon](https://neon.tech) (or any other Postgres host).
2. Copy the connection string it gives you. It looks like:
   `postgresql://user:password@host:5432/dbname?sslmode=require`
3. **Locally**: copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
   and paste your real connection string in as `DATABASE_URL`. This file is
   already in `.gitignore`, so it will never be committed.
4. **On Streamlit Community Cloud**: don't upload a secrets file at all. Open
   your deployed app's Settings -> Secrets, and paste:
   ```
   DATABASE_URL = "postgresql://user:password@host:5432/dbname?sslmode=require"
   ```
5. The app creates its tables automatically on first run — no manual schema setup
   needed.

Never commit real passwords, API keys, or connection strings to this repository.
`.streamlit/secrets.toml` and any `.env` files are already gitignored — keep it
that way.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
streamlit run app.py
```

Make sure you've completed the database setup above first — the app will show
an error and stop if `DATABASE_URL` isn't configured.

## Deploy on Streamlit Community Cloud

1. Create a private GitHub repository and upload `app.py`, `requirements.txt`, `README.md`, `.gitignore`, the `.streamlit/` folder (config.toml only — never secrets.toml), and the `assets/` folder.
2. In Streamlit Community Cloud, choose **Create app**, select that repository, and set the main file to `app.py`.
3. Add your `DATABASE_URL` in the app's Settings -> Secrets (see database setup above).
4. Use the default Python version and deploy.

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
