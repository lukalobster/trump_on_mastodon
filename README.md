# @realDonaldTrump Truth Social Monitor → Mastodon

Monitors [@realDonaldTrump](https://truthsocial.com/@realDonaldTrump) on Truth Social every 30 minutes and automatically posts any new content to a Mastodon account.

## How it works

Two completely independent GitHub Actions workflows run on a schedule:

```
:00  Monitor runs  → fetches Truth Social API, updates posts.md if new post
:15  Poster runs   → reads posts.md, posts to Mastodon if new
:30  Monitor runs  → checks again
:45  Poster runs   → checks again
```

## Repository structure

```
├── monitor.py                          ← Fetches Truth Social, writes posts.md
├── mastodon_poster.py                  ← Reads posts.md, posts to Mastodon
├── .github/
│   └── workflows/
│       ├── monitor.yml                 ← Runs monitor.py every 30 min
│       └── mastodon_poster.yml         ← Runs mastodon_poster.py every 30 min
├── posts.md                            ← Auto-generated: last 3 posts
├── last_post_id.txt                    ← Auto-generated: monitor state
└── last_mastodon_post_id.txt           ← Auto-generated: poster state
```

## Setup

### 1. Create a new GitHub repository

Create a new **public** repository (public = unlimited free Actions minutes).

### 2. Add all files

Add the following files via GitHub's web interface (**Add file → Create new file**):

| File | Path to type in GitHub |
|---|---|
| `monitor.py` | `monitor.py` |
| `mastodon_poster.py` | `mastodon_poster.py` |
| `monitor.yml` | `.github/workflows/monitor.yml` |
| `mastodon_poster.yml` | `.github/workflows/mastodon_poster.yml` |
| `requirements.txt` | `requirements.txt` |
| `.gitignore` | `.gitignore` |

### 3. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|---|---|
| `SCRAPEDO_TOKEN` | Your [scrape.do](https://scrape.do) API token |
| `MASTODON_INSTANCE_URL` | Your Mastodon instance URL, e.g. `https://mastodon.social` |
| `MASTODON_ACCESS_TOKEN` | Your Mastodon bot account access token |

#### How to get a Mastodon access token

1. Log in to your Mastodon bot account
2. Go to **Preferences → Development → New Application**
3. Name it anything (e.g. `TrumpTruthBot`)
4. Enable scopes: `write:statuses` and `write:media`
5. Click **Submit**, then copy **"Your access token"**

### 4. Enable Actions and test

1. Go to the **Actions** tab in your repository
2. Click **Enable workflows** if prompted
3. Click **Run workflow** on the **Trump Truth Social Monitor** workflow to test immediately

## Output format (`posts.md`)

```markdown
## Post detected at 2026-04-01 16:20:00 UTC

**Posted on Truth Social:** 2026-04-01 16:20:57 UTC
**Source:** [https://truthsocial.com/@realDonaldTrump/116330362125395500](...)
**Post ID:** `116330362125395500`

### Text

We are the only Country in the World STUPID enough to allow "Birthright" Citizenship!
President DONALD J. TRUMP

---
```

## Notes

- **scrape.do credits**: Each 30-minute check = 1 credit (~1,440/month). Truth Social is geo-restricted to US/UK/Canada, so scrape.do's US proxy is required.
- **posts.md size**: Only the 3 most recent posts are kept. Older posts are automatically removed.
- **Images**: Downloaded and committed to the `images/` folder. Posts without images are handled gracefully.
- **Retruth (repost)**: Detected and labelled as `[Retruth from @username]` in the text.
