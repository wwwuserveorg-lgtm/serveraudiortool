# UServe Contractor Service Review — Streamlit

## Upload to Streamlit Community Cloud

1. Upload every file in this folder to a GitHub repository.
2. In Streamlit Community Cloud, choose **Create app**.
3. Select the repository and branch.
4. Set **Main file path** to `app.py`.
5. Deploy.

The app works immediately with SQLite for testing. For permanent production history, add a hosted PostgreSQL connection string in Streamlit **Settings → Secrets**:

```toml
DATABASE_URL = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require"
```

## Report workflow

Add a contractor in **Management**, then use **Reports** to paste reports or upload CSV/XLSX files. The app recognizes:

- Jobs with attempts
- Jobs with no attempts
- Historical job detail / weekly server audit upload report
- Server performance report
- Daily activity log

Run the review after the required reports are present. Save the audit to preserve history.

## Duplicate timestamp rules

The app alerts only when an identical event timestamp appears on different jobs. It ignores matches when:

- the Job ID is the same;
- the Law Firm File Number is the same; or
- the servee name and full address are the same.

Valid open jobs without attempts are not treated as skipped records. Aged no-attempt jobs are handled as operational priorities.
