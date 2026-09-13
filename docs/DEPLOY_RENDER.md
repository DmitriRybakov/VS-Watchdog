# Deploying Watchdog for the pilot

One free Render web service, one free Supabase PostgreSQL database, one shared password. Enough for
a handful of colleagues to use the register from a browser, and small enough to throw away.

Four facts shape everything below. Read them first, because each one explains a step that would
otherwise look strange.

- **The service sleeps.** Render's free plan stops the instance after fifteen minutes with no
  requests. The first request afterwards takes up to a minute while it wakes. A long run started
  from the browser is killed if everyone closes the page and it goes quiet - see
  [If a run is cut off](#if-a-run-is-cut-off).
- **The disk is not storage.** Anything written to a file on Render is gone at the next deploy.
  Every durable thing lives in Supabase. Watchdog refuses to start hosted with a SQLite URL for
  exactly this reason.
- **There is no pre-deploy step on the free plan.** So migrations are not run by Render at all.
  You run them from Windows, against the same database, before the deploy that needs them.
- **No data is being transferred.** The hosted register starts empty and is filled from TED. Notice
  counts and scores will not match your laptop, and are not supposed to.

## Before you start: what has to survive, and what does not

Notices come back from TED. Screening results come back from a re-screen. **Configuration comes back
from nowhere** - so it is worth being precise about where it actually lives today.

I checked your local database against the YAML in git before writing any of this:

| | Where the active version lives | Status |
| --- | --- | --- |
| Rules, mandate, scoring policy, CPV lists | `config/*.yaml` in git. **There is no `config_version` table yet** and no settings page, so nothing overrides them. | `config/` is committed and clean; nothing is uncommitted or stashed. |
| What the local database was screened under | `rules_version 3`, `policy_version 1`, `profile_version 1`, stamped on all 10,790 screening results | The same three versions as `config/rules.yaml`, `config/policy.yaml` and `config/profile.yaml` in git. |

**Nothing differs, and there is nothing to export.** The hosted service builds from the same commit,
so it gets byte-identical configuration. There is no config export/import command to use, because
the configuration has never lived anywhere but those files. When the settings page and the
`config_version` table arrive, this section needs rewriting - at that point configuration becomes
database state and would need carrying across like any other.

Your local `data\watchdog.db` and `data\watchdog.db.pre0009` are not read, written or moved by
anything here.

## Step 1 - The Supabase database

1. Create a project. Choose a region near you (`eu-central-1` for Frankfurt).
2. Save the database password when it is shown. It is not shown again.
3. Open **Connect** and copy the **Session pooler** URL - the one on **port 5432**, whose username
   looks like `postgres.abcdefghijklm`.

Use the session pooler, not the direct connection and not the transaction pooler on port 6543:

- the direct connection is IPv6-only on the free plan, and Render gives you IPv4;
- the transaction pooler does not keep a session between statements, which breaks prepared
  statements and some of what SQLAlchemy does with them.

If the password contains any of `: / ? # [ ] @`, percent-encode those characters in the URL
(`@` becomes `%40`, and so on). Everything else can stay as it is.

You do not need to add `sslmode` yourself. Watchdog rewrites the URL before use: `postgresql://`
becomes `postgresql+psycopg://` so it uses the driver that is actually installed, and `sslmode=require`
is added if it is missing, because libpq would otherwise be willing to connect without encryption.

## Step 2 - Hold the secrets locally, without committing them

```powershell
cd c:\dev\VS-Watchdog
Copy-Item .\deploy\render.env.example .\deploy\.env.render
notepad .\deploy\.env.render
```

Fill in `DATABASE_URL` and the three sign-in values. `deploy\.env.render` is git-ignored and must
stay that way; `deploy\render.env.example` is the committed copy with no values in it.

Generate the session secret rather than inventing one:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then load the file into the PowerShell window you are working in:

```powershell
. .\deploy\Use-DeployEnv.ps1
```

The leading dot matters: it loads the values into this window rather than a child process. They beat
anything in `.env`, so every command you run in this window from now on goes to the hosted database.
Your `.env` is untouched, and nothing on disk changes. **Close the window when you are finished**, or
run `. .\deploy\Use-DeployEnv.ps1 -Clear`. No value is ever printed.

## Step 3 - Create the tables

In the same window:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Check it landed:

```powershell
.\.venv\Scripts\python.exe -m alembic current
```

It should print `0009` (or whatever the newest migration is by then).

Run this again from Windows every time a future deploy adds a migration - **before** you push the
code that needs it. Render never runs it.

## Step 4 - Close the Supabase Data API

Supabase publishes every table in the `public` schema at a URL that the anon key opens, and the anon
key is meant to be public. Watchdog's tables hold buyer names, screening evidence and colleagues'
decisions, and none of that should be readable that way.

Open the Supabase **SQL Editor**, paste the contents of [deploy/supabase_lockdown.sql](../deploy/supabase_lockdown.sql)
and run it. It takes privileges away from the two API roles and enables row level security; it
creates nothing, drops nothing and changes no data. Watchdog connects as `postgres`, which owns the
tables and is unaffected.

The last statement in the file is the check. Every row must read `false, false`.

Run it again after any future migration that adds a table.

## Step 5 - Fill the register from TED

Still in the same window, so it is still the hosted database:

```powershell
.\.venv\Scripts\watchdog.exe ingest --full-backfill 2026-06-01
```

Choose the date you want the register to start from. A wider window takes longer and costs nothing
but time; TED is free. Watch for the line saying whether the watermark advanced - if the run was
partial, the watermark stays where it was on purpose, and running it again repeats the same window
rather than skipping it.

Then score everything that was just read:

```powershell
.\.venv\Scripts\watchdog.exe screen --rescreen
```

And confirm what happened:

```powershell
.\.venv\Scripts\watchdog.exe runs
```

Both commands are safe to run twice. A notice keeps the same row, and a new screening result
supersedes the previous one rather than editing it.

Both also take **the same lock the browser buttons take**, which matters here more than anywhere
else: this is the one moment when a long run from Windows overlaps a live site. If a colleague is
mid-**Update from TED**, the command refuses, names the run and its last progress report, and
changes nothing. While the command runs, the buttons are disabled and the header says
`Ingest (command line): reading notices from TED`, so nobody is left wondering why the page will not
start a run. `--dry-run` takes no lock, because it stores nothing to collide over.

This is a new baseline. The counts will not match your laptop: TED's window has moved on, and a
notice that has closed since is not returned any more.

## Step 6 - The Render service

Connect the repository in Render as a **Web Service**, native Python runtime, free plan. If you use
the committed [render.yaml](../render.yaml) as a Blueprint, everything below is already set except
the four values marked "entered by you".

| Setting | Value |
| --- | --- |
| Runtime | Python (native, not Docker) |
| Build command | `pip install --upgrade pip && pip install .` |
| Start command | `python -m uvicorn watchdog.web.app:app --host 0.0.0.0 --port $PORT --workers 1 --forwarded-allow-ips="*"` |
| Health check path | `/health` |
| Auto-deploy | On, from `main` |

The build installs the package, which carries its own templates, CSS and vendored HTMX as package
data, so there is nothing else to collect or copy. There is no Node, no bundler and no CDN request
at runtime.

One worker, because two processes on one free instance would be two job locks competing over 512 MB.
`--forwarded-allow-ips="*"` is not decoration: Render terminates TLS at its proxy and forwards plain
HTTP to the process, and without it every link the templates build - the stylesheet and the vendored
HTMX among them - comes out as `http://` and the browser refuses to load it on an `https://` page.
The quotes matter; unquoted, the shell would expand the `*` into filenames.

Neither command runs a migration. A build command that did would run on every deploy; a start
command that did would run on every wake-up from sleep.

### Environment variables

| Name | Value | Why |
| --- | --- | --- |
| `PYTHON_VERSION` | `3.13.0` | The interpreter Watchdog is developed on. If Render says that exact version is unavailable, use the nearest `3.13.x` and change it in `render.yaml` too. |
| `ENVIRONMENT` | `production` | Turns on hosted mode: JSON logs, `Secure` cookies, and sign-in and PostgreSQL both compulsory. |
| `DATABASE_URL` | *entered by you* - the Session pooler URL from Step 1 | Where everything durable lives. Without it, hosted mode refuses to start rather than falling back to a file that a deploy would erase. |
| `AUTH_USERNAME` | *entered by you* | The shared username. |
| `AUTH_PASSWORD` | *entered by you* | The shared password. Anybody who has it can read the register and start a run. |
| `SESSION_SECRET` | *entered by you* - 48 random characters | Signs the session cookie. Changing it signs everybody out, which is the only way to end sessions that are already issued. |
| `SESSION_HOURS` | `12` (optional) | How long a sign-in lasts before it is typed again. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for one deploy when something is wrong, then back. |
| `LLM_PROVIDER` | `disabled` | No model in the pilot. Everything works without one. |

Render sets `PORT` and `RENDER` itself. Do not set either. `RENDER` is a second belt: if `ENVIRONMENT`
is ever lost or mistyped, Watchdog still treats the service as hosted and still demands a sign-in.

If any of the four required values is missing, the service **fails to start** and the deploy log says
which one in a full sentence. That is deliberate. A site that came up without a password would look
exactly like a successful deploy.

### What the sign-in covers

Everything except `/health`. Pages, HTMX fragments, both exports, the API docs and even the
stylesheet need a session - which is why the sign-in page carries its own styling inside the page.

`/health` returns `{"status": "ok", "service": "watchdog"}` and nothing else. It used to include the
version, the environment and the model provider; those are facts worth having before attacking a
site, so they have gone. They are all still on the page once you have signed in.

Every button that changes something sends a token tied to your session, so a form on somebody else's
site cannot record a verdict or start a run with your cookie. Five wrong passwords from one address
and that address waits fifteen minutes.

## Step 7 - The restart test

Do this once, immediately after the first deploy. It takes about twenty minutes, most of it waiting.

1. Sign in and open the register.
2. **Record a decision.** Open any notice in the triage queue and press `y`, `n` or `u`. Note the
   notice's title.
3. **Change something that is stored server-side.** Press **Update from TED** on the Runs page and
   let it finish. That writes a run row, a watermark and - if TED has anything new - notices and
   screening results. Note the time the run finished.
4. **Restart the instance.** In Render: **Manual Deploy → Restart service**. Or simply leave it alone
   for twenty minutes, which is what will happen in real use anyway.
5. Load the site again. It will be slow for the first few seconds.
6. Check all of it:
   - the review is still on the notice from step 2, with the same name and time;
   - the Runs page still lists the run from step 3, with its counts;
   - the register still holds the same number of notices, with their scores and bands;
   - you are still signed in - the session survives a restart because it is a signed cookie and not
     a row in a table.

If all four hold, the database is doing its job and the disk is doing nothing.

**One honest caveat about "a settings change saved through the app".** There is nothing in Watchdog
today that a colleague can edit and save into the database. The settings page and the
`config_version` table do not exist yet; rules, mandate text and the scoring policy are read from the
YAML files in git, and the only thing the browser stores is the reviewer's name, which is a cookie in
that browser and deliberately not a stored setting. So step 3 uses a run, which is the nearest thing
there is: state created by pressing a button in the app and written to PostgreSQL. When the settings
page arrives, add "change a rule, save it, restart, check it is still changed" to this list - and add
the configuration itself to what has to be carried between databases.

## If a run is cut off

The free plan stops the instance after fifteen quiet minutes. A run started from the browser keeps
the instance awake only while a page is open polling it, so a long run plus a closed laptop means the
run is killed part way through.

Nothing is lost when that happens:

- notices are written in batches as they arrive, and every batch is committed;
- the watermark only moves at the end of a window that was read to the end with every write
  succeeding, so an interrupted run leaves it where it was and the next run reads the same window
  again;
- a notice is never duplicated - identity is (source, source_id), so a repeated window updates the
  same rows.

The lock the killed run was holding is dealt with by silence. Every progress report moves a
heartbeat on the lock row. Fifteen minutes with no report means the process behind it is gone, and
then two things become possible: the page shows "a run stopped part way through" with the buttons
enabled again, and the next instance to start clears it.

The opposite case is also handled, and matters more: a lock that **is** still reporting is left
completely alone. During a deploy the new instance can start while the old one is still working, and
clearing the lock there would let two runs write to the same register at once. The same rule is what
lets an hour-long backfill from Windows keep the lock for an hour: it is reporting, so nothing takes
it from underneath. Stopping such a command with Ctrl-C hands the lock straight back rather than
leaving the buttons disabled for a quarter of an hour.

For a big backfill, prefer the Windows commands in Step 5. Nothing can put your laptop to sleep
mid-run in the way Render can.

## What still needs a live PostgreSQL connection to prove

Everything above is checked against SQLite and the fake provider; the test suite never opens a
socket. These are the things only a real Supabase connection can settle, and they are worth going
through in order the first time:

1. **That the URL and the driver work at all.** `alembic upgrade head` in Step 3 is the test. A
   wrong password, an unencoded character in it or the wrong pooler shows up here and nowhere else.
2. **That every migration runs on PostgreSQL.** They are written dialect-neutrally and `0001`-`0009`
   have only ever been run against SQLite in anger. `alembic current` printing the newest revision
   is the proof.
3. **That the schema check agrees.** `watchdog runs` against the hosted database reads the catalogue
   and will say so plainly if anything is missing.
4. **That a full ingest holds up over a real connection.** Step 5 is a few thousand notices through a
   pooler rather than a local file. Watch that the counts reconcile and that the run is not reported
   as partial.
5. **That the Data API is really closed.** Only the query at the end of
   `deploy/supabase_lockdown.sql` can answer this, and only on the real project.
6. **That the lock behaves with two instances.** The staleness rule is tested with a moved clock on
   one process. A deploy that overlaps a running job is the real version, and Step 7 is the nearest
   thing to trying it.
7. **Pool sizing under a sleeping service.** Connections are checked before use and recycled after
   four minutes, which is the standard answer to a pooler that drops idle sessions. Whether the free
   Supabase connection limit is comfortable with it is something only a week of use will show.

## Afterwards

- Migrations are always: load the deploy window, `alembic upgrade head`, **then** push.
- Adding a table means running `deploy/supabase_lockdown.sql` again.
- To change the password: change `AUTH_PASSWORD` in Render, and change `SESSION_SECRET` at the same
  time if you want the sessions already handed out to end.
- Nothing is ever deleted, here or anywhere else in Watchdog. A register that has grown untidy is
  filtered, not cleared.
