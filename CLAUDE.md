# Precog

Crime ingestion, analysis and prediction for Charlotte-Mecklenburg, built on CMPD's public incident feed. The aim is a tool that says where and how much crime to expect, explains why in plain English, and publishes how its predictions turned out.

## Where the plan lives

The task board is the single list of work: https://claude.ai/artifact/B2NKEkqsU9eabsgGFTfPnd

Cards live in the artifact's database, in the document `board/state` under `cards.<id>` with fields `col`, `type`, `title`, `desc`, `effort`, `order`. Columns: `fix`, `sprint`, `ui`, `code`, `data`, `road`, `done`.

- When asked "what's next?", read the board and answer from `fix` first, then `sprint`.
- When a piece of work is finished, move its card to `done` and add cards for anything discovered along the way, without being asked. Use `ArtifactData` `update` on `board/state`; do not republish the page for card changes.

## Decisions already made

- **Predictions must beat a simple guess before new features are added.** The accuracy record (`/accuracy`) compares each prediction with a no-Precog baseline. As of 2026-11-14 the daily forecast beats its baseline; the top-10 area ranking and the spike alerts do not. Fixing those comes before more analyses.
- **Finish the resident view first.** Four roles exist (Sergeant, Resident, Chief, Detective), but residents need no procurement or CJIS compliance, and public trust supports a later CMPD pitch.
- **Only real data on screen.** Nothing estimated or invented. Known offenders still to remove: hardcoded area populations, estimated game schedules, fixed-date holidays, time-of-day patterns (CMPD publishes dates only).
- **Never guess a match.** The news check lists a story as undecided when more than one police report fits.
- **NPAs are called "areas" in everything a user sees.**

## How it runs

- Start: `venv/bin/python3 -m uvicorn main:app --reload --port 8000` (Python 3.9, SQLite at `precog.db`).
- While the server runs it refreshes CMPD data and news hourly, weather and 311 daily, and saves and scores weekly predictions (`app/refresh.py`). `PRECOG_AUTO_REFRESH=0` turns this off. Reddit and Bluesky ingest is opt-in with `ENABLE_AUTO_INGEST=1`.
- Pages: `/` map, `/accuracy` accuracy record, `/news-check` news cross-check.

## Things that will trip you up

- Use `ref_now()` from `app/clock.py` for any time window, never `datetime.utcnow()`. It anchors on the newest incident so CMPD's roughly two-day publication lag does not read as a drop in crime.
- Inside `as_of(moment)`, queries cannot see incidents, 311 cases or weather dated on or after that moment, and the model cache is bypassed. Reconstructed accuracy weeks depend on this.
- `date_reported` is midnight Eastern stored as UTC (04:00 or 05:00). There is no time of day in the data.
- After ingesting outside the refresh loop, delete `models/*.joblib` and `models/*.meta`, or cached risk scores and forecasts stay stale for up to six hours.
- There are no automated tests yet. Verify changes by running the server and loading the page.

## Writing for the user

Explain results in plain terms: "off by about 16 incidents a day out of 251", not R² or raw percentages without context. Put the no-Precog comparison next to any accuracy figure.
