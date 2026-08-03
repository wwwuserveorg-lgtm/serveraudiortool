# UServe Contractor Service Review — Streamlit

## Upload to Streamlit Community Cloud

1. Upload every file in this folder to a GitHub repository.
2. In Streamlit Community Cloud, choose **Create app**.
3. Select the repository and branch.
4. Set **Main file path** to `app.py`.
5. Deploy.

In the app's Streamlit sharing settings, make the app **public**. The UServe
15-minute token protects the contractor review itself. If the Streamlit app is
private, Streamlit will require its own login before the token can be checked.

The app works immediately with SQLite for testing. For permanent production history, add a hosted PostgreSQL connection string in Streamlit **Settings → Secrets**:

```toml
DATABASE_URL = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require"
PUBLIC_APP_URL = "https://YOUR-APP-NAME.streamlit.app"
```

`PUBLIC_APP_URL` must be the exact deployed Streamlit URL with no trailing
slash. It is used to generate the complete contractor link. Contractors do not
need ChatGPT accounts.

The sidebar must show **Permanent database connected** before production use.
If it shows temporary SQLite mode, audit history can disappear when Streamlit
restarts.

## Report workflow

Add a contractor in **Management**, then use **Reports** to paste reports or upload CSV/XLSX files. The app recognizes:

- Jobs with attempts
- Jobs with no attempts
- Historical job detail / weekly server audit upload report
- Server performance report
- Daily activity log

Run the review after the required reports are present. Save the audit to preserve history.

## Saving an audit

- **Save audit notes now** saves the current audit and its notes without completing it.
- **Internal auditor notes** remain visible only to auditors.
- **Call notes shared in Server View** appear in the contractor's temporary view.
- **Complete audit and save** archives the audit, ends access for any old token,
  and removes that contractor from the current completed-period workload.

### Carry-forward follow-up notes

Use **Save open note** for any matter that must be discussed or checked again.
The note appears automatically on every later review for that contractor. Click
**Mark closed** when it is resolved; it remains in closed-note history but no
longer appears as an open item on future audits.

### Pasted report recognition

Auto-detect is the default. The app shows the detected report type before the
auditor submits it. After a successful save, the paste box clears. If the type
cannot be recognized, the pasted text stays in the box so the auditor can
select the correct report type and submit again without repasting.

## Contractor Server View

1. Save the audit draft.
2. Open **Server View**.
3. Click **Generate 15-minute server link**.
4. Send the complete link to the contractor or open **Test server view**.
5. Use **End all active server views** when the call ends.

## Duplicate timestamp rules

The app alerts only when an identical event timestamp appears on different jobs. It ignores matches when:

- the Job ID is the same;
- the Law Firm File Number is the same; or
- the servee name and full address are the same.

Valid open jobs without attempts are not treated as skipped records. Aged no-attempt jobs are handled as operational priorities.
