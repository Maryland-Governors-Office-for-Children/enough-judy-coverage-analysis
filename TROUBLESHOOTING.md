# Troubleshooting the GitHub Pages deploy

If a push to `main` should have updated the live site but the workflow is
red — or the live URL hasn't refreshed — follow this in order. Most failures
fit one of seven categories.

## 1. Pull the failure log

```bash
REPO=Maryland-Governors-Office-for-Children/enough-judy-coverage-analysis
gh run list --repo $REPO --limit 5
gh run view <RUN_ID> --repo $REPO --log-failed | head -80
```

The first lines after `##[error]` are the actionable signal. Skim the rest
only if those don't tell you enough.

## 2. Match the error to a category

| # | Category | What you'll see in the log | Where to fix |
|---|---|---|---|
| A | Workflow YAML syntax | `Invalid workflow file`, `Unexpected input(s)`, parser errors | `.github/workflows/pages.yml` |
| B | Pages not enabled on the repo | `Get Pages site failed. Error: Not Found` and Create succeeds | Repo **Settings → Pages → Source = GitHub Actions**, *or* set `enablement: true` on `actions/configure-pages` (already on this repo) |
| C | **Org plan / visibility blocks Pages** | `Resource not accessible by integration` on Create Pages, plus HTTP 422 `Your current plan does not support GitHub Pages for this repository.` from a direct API call | **External:** make repo public, upgrade org plan, or switch to Cloudflare Pages |
| D | Wrong artifact path | `No files were found with the provided path` | Fix `path:` parameter on `actions/upload-pages-artifact` |
| E | Missing token permissions | `Resource not accessible by integration` on configure, but the org plan IS sufficient | Add `permissions: { pages: write, id-token: write }` to the workflow |
| F | Branch protection / environment rule | `Deployment environment rule failed`, environment review required | Adjust the `github-pages` environment in repo Settings |
| G | Build step itself errored | Logs show a Python/Node failure before configure-pages runs | Fix the offending code, push |

## 3. Disambiguating the look-alike error

Both Categories C and E surface as `Resource not accessible by integration`.
A direct API call disambiguates:

```bash
gh api -X POST repos/$REPO/pages -f build_type=workflow
```

- HTTP 403 → Category E (workflow missing `permissions:` block)
- HTTP 422 with `Your current plan does not support…` → Category C (org plan
  doesn't allow Pages on this repo's visibility)

## 4. Apply the fix

- **Categories A, D, E, G** are file-side. Edit, commit, push. The workflow
  retriggers on any push that touches `site/**` or the workflow file itself.
  To force a rerun without a code change:
  ```bash
  gh workflow run pages.yml --ref main --repo $REPO
  ```
- **Categories B, C, F** are repo settings or plan changes — they cannot be
  fixed by editing files.

## 5. Verify

```bash
LIVE=https://maryland-governors-office-for-children.github.io/enough-judy-coverage-analysis/
gh run watch <RUN_ID> --repo $REPO --exit-status
curl -sI "$LIVE" | head -3
curl -sS "$LIVE/data/grantees.json" | python -m json.tool | head -10
```

GitHub's CDN sometimes lags 1–2 minutes after a green deploy; if `curl`
returns 404 immediately after the run goes green, wait and retry.

## History on this repo

| Date | Run ID | Category | Resolution |
|---|---|---|---|
| 2026-04-28 | 25045041741, 25047827940 | C — org plan blocks Pages on private repo | Made the repo public; Pages auto-enabled on the next push via `enablement: true` on configure-pages |

## Reference

- Workflow file: `.github/workflows/pages.yml` (configure → upload `site/` →
  deploy).
- Live URL pattern: `https://<owner>.github.io/<repo>/`. For this repo:
  `https://maryland-governors-office-for-children.github.io/enough-judy-coverage-analysis/`
- The site is fully static. Everything served lives under `site/` in the
  repo: `index.html`, `app.js`, `styles.css`, and the `data/` folder of
  GeoJSONs and `grantees.json`. Re-run `python analyze.py` to regenerate
  the data files.
