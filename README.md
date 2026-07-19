# Neuroscience at Work — Source Radar

A small robot that, **once a week and completely on its own**, searches the free
scholarly databases (OpenAlex, Crossref, Europe PMC, arXiv) for **new** research
at the intersection of neuroscience + psychology + workforce management,
removes anything already in your registry, and drops the new finds into a
**"for review" inbox** (`candidates/inbox.csv`) for you to approve.

It runs on **GitHub Actions** (free) — not on your computer — so nothing needs to
stay open. **No API keys, no tokens, no passwords.** The free databases are
keyless, and GitHub runs the job with its own built-in token.

---

## What you need
- A free **GitHub account** → https://github.com/signup
- ~15 minutes, once. After that it runs itself every Monday.

You do **not** need to install anything or use a terminal.

---

## Set-up, click by click (all in the browser)

### 1. Create the repository
1. Sign in to GitHub → top-right **“+”** → **New repository**.
2. **Repository name:** `neuroscience-at-work-radar`
3. Choose **Private** (recommended). Leave everything else as-is.
4. Click **Create repository**.

### 2. Upload the files
1. On the new empty repo page, click **“uploading an existing file”**
   (the link in the grey box), or **Add file → Upload files**.
2. From the folder I gave you, drag in these files:
   - `scan_sources.py`
   - `config.yaml`
   - `requirements.txt`
   - `registry_seed.csv`
   - `README.md`
3. Also drag the whole **`candidates`** folder in (it contains a README).
4. Scroll down, click **Commit changes**.

### 3. Add the weekly schedule file
The scheduler lives in a special path, easiest to create directly:
1. **Add file → Create new file**.
2. In the filename box type exactly:
   `.github/workflows/weekly-scan.yml`
   (typing the slashes makes the folders automatically).
3. Open the file `weekly-scan.yml` I gave you, copy **all** its text, and paste it in.
4. Click **Commit changes**.

### 4. Let the robot save its findings
1. Go to the repo’s **Settings** tab → left menu **Actions → General**.
2. Scroll to **Workflow permissions** → choose **Read and write permissions** → **Save**.

### 5. Test it once, by hand
1. Go to the **Actions** tab → in the left list click **“Weekly source radar”**.
2. Click **Run workflow** → **Run workflow** (green button).
3. Wait ~1–2 minutes and refresh. A green ✔ means it worked.
4. Open the **`candidates`** folder in your repo → **`inbox.csv`** to see the finds.

That’s it. From now on it runs **every Monday** automatically.

---

## Your weekly routine (about 5 minutes)
1. Open `candidates/inbox.csv` (or the newest `candidates_YYYY-Www.csv`).
2. Skim the new rows — note `open_access`, `preprint`, `type`, `venue`.
3. Move the keepers into your **master registry**, assign a reliability tier
   (A/B/C), and check them against the neuromyth list. *(This judgement is left
   to you on purpose — the robot finds, you decide.)*
4. Add anything you’ve handled to **`registry_seed.csv`** so it isn’t suggested again.

---

## Tuning it (optional)
Open **`config.yaml`** on GitHub, click the pencil ✏️ to edit, change the
`queries`, `must_match_any` keywords, or `since_days`, then **Commit changes**.

## Change how often it runs
In `.github/workflows/weekly-scan.yml`, the line `cron: "0 7 * * 1"` = every
Monday 07:00 UTC. For example, monthly on the 1st = `cron: "0 7 1 * *"`.

## Notes & honest limits
- The robot **finds candidates**; it does **not** grade reliability or catch
  neuromyths — that stays your job, to protect the quality of the master base.
- Books rarely appear in these scholarly APIs; the radar is strongest for
  journal articles, preprints, and reports. Track key books manually.
- Free APIs occasionally rate-limit or return nothing for a week — that’s normal;
  the next run picks up.
