# pr-rubric-judge

Judges every open PR in a GitHub repo against a weighted rubric, on a
schedule, and writes a report. Read-only against GitHub — never comments,
closes, merges, or pushes anything.

## Prerequisites

- `gh` (GitHub CLI), authenticated with read access to your target repo
- `claude` (Claude Code CLI) ≥ 2.1.206
- Whatever build/test toolchain your target repo's CI needs
- `flock` (preinstalled on virtually all Linux distros)
- Python 3.10+ (stdlib only, no `pip install` needed)

## Setup

```bash
git clone <this repo>
cd pr-rubric-judge
cp .env.example .env
```

Edit `.env`:
- `TARGET_REPO` — the repo whose PRs you're judging, `owner/repo`
- `ANTHROPIC_API_KEY` — get one from the Anthropic Console

If your target repo's CI isn't `uv sync && make ci-local`, edit the two
CI lines near the top of `verify_pr.sh` to match your own build/test
command.

If your rubric isn't the example one already in `rubric.py` (five
dimensions, four bands, a Phase 1 gate), edit the weights, bands, and
dimension text in that file to match yours.

## Run it

```bash
./run_once.sh
```

First run clones `TARGET_REPO` and sweeps every open PR — expect it to
take a while and cost a few dollars in API usage for a large queue. Every
run after that only reprocesses PRs that are new or changed since the
last run.

Output lands in `reports/`: `latest.html` and `latest.csv` always point at
the most recent run.

## Run it on a schedule

```bash
crontab -e
0 * * * * cd /path/to/pr-rubric-judge && ./run_once.sh >> logs/cron.log 2>&1
```

Use `0 */3 * * *` for every 3 hours, or any other cron schedule.

## Configuration

All environment variables, settable in `.env` or the shell:

| Variable | Default | Meaning |
|---|---|---|
| `TARGET_REPO` | *(required)* | repo whose PRs you're judging, `owner/repo` |
| `ANTHROPIC_API_KEY` | *(required)* | your Anthropic API key |
| `TARGET_CLONE` | `./clone` | local clone directory |
| `TARGET_REMOTE` | `upstream` | git remote name in that clone |
| `PHASE1_BRANCH_PATTERN` | `^.+$` | regex eligible PR branch names must match |
| `PHASE1_REQUIRED_BASE` | `main` | required base branch |
| `PR_JUDGE_STATE` | `./state` | cache + artifacts directory |
| `PR_JUDGE_REPORTS` | `./reports` | where reports land |
| `PR_JUDGE_MAX_WORKERS` | `3` | concurrent PRs processed at once |
| `PR_JUDGE_MODEL` | *(claude default)* | override the judge model |
| `PR_JUDGE_MAX_BUDGET_USD` | `0.75` | per-PR judge call cost ceiling |
| `PR_JUDGE_CLUSTER_MAX_BUDGET_USD` | `0.50` | per-cluster comparison cost ceiling |

## Multiple users

Each person running this should use their own `ANTHROPIC_API_KEY` in
their own `.env` (gitignored, never committed). `TARGET_REPO` isn't a
secret and can be shared freely.
