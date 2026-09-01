# LNDC Industrial Intelligence Radar — Data Acquisition Starter Kit

This is the first working piece of the Radar's data acquisition layer:
a free, automated, daily pull of GDELT news signal relevant to the
automotive components pilot, with no server to maintain.

## What it does

Once a day, GitHub's own free servers run `scripts/fetch_gdelt_automotive.py`,
which queries the GDELT Project's public API for recent articles matching
automotive-components-and-Africa keywords, and appends any new ones to
`data/automotive_signal_log.csv`. That CSV is your growing, shared
repository of signal — the exact thing the diagnostic found LNDC currently
lacks.

## Setup — see the step-by-step walkthrough for full instructions.

Quick reference:
1. Create a new GitHub repository and upload this folder's contents to it.
2. Go to the repo's Settings → Actions → General → Workflow permissions,
   and set it to "Read and write permissions". Save.
3. Go to the Actions tab, select "Automotive signal pull (GDELT)", and
   click "Run workflow" to trigger it manually the first time.
4. Check the `data/` folder afterward for `automotive_signal_log.csv`.
5. After that, it runs automatically every day at 06:00 UTC — no further
   action needed.

## Adjusting the search

Edit the `QUERY` variable near the top of `scripts/fetch_gdelt_automotive.py`
to change what counts as a relevant signal. Keep it in GDELT's query syntax:
`OR` inside parentheses for alternatives, `AND` (or just a space) between
required groups, quotes around exact phrases.
