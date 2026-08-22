# The Monday Morning Banter Bulletin automation

The automation runs at 06:00 Europe/London every Monday. Codex writes the humour, while Season27
remains the authority for match results, scorelines, leaderboard movement, storage, and publication.

## One-time setup

1. Deploy this release and apply migrations. Keep the Railway service on one replica with its SQLite
   database on the persistent volume.
2. Generate an independent random token of at least 32 characters. Add it to the Railway service as
   `SEASON27_BULLETIN_AUTOMATION_TOKEN` and set
   `SEASON27_BULLETIN_AUTOMATION_ACTOR_NAME` to the exact name of an active administrator.
3. On the computer that runs Codex, set the same token and the production origin:

   ```bash
   export SEASON27_PUBLIC_BASE_URL=https://your-season27-domain.example
   export SEASON27_BULLETIN_AUTOMATION_TOKEN='use-a-secret-manager'
   ```

4. Keep that computer powered on, the Codex desktop app running, this project available, and network
   access to both the Season27 site and BBC Sport.

## Supervised rehearsal

Prepare a pack without publishing anything:

```bash
mkdir -p .season27-automation
uv run season27-bulletin prepare --output .season27-automation/facts.json
```

Review `facts.json`. Create `.season27-automation/body.txt` containing 15–120 words, then publish and
verify:

```bash
uv run season27-bulletin publish \
  --fact-pack .season27-automation/facts.json \
  --body-file .season27-automation/body.txt \
  --output .season27-automation/published.json
uv run season27-bulletin verify monday-morning-banter-YYYY-MM-DD
```

The publish endpoint rebuilds the fact pack and compares its SHA-256 digest. It refuses publication
if the facts changed between preparation and publication. Repeating a successful publish is safe.
If an administrator suppresses an edition, automation cannot republish it.

## Codex scheduled-task prompt

Run these instructions from the Season27 project:

> Generate and publish The Monday Morning Banter Bulletin. Run `uv run season27-bulletin prepare
> --output .season27-automation/facts.json`. If its status is `already_published`, report that and
> stop. Read only the returned fact pack. Write a sharp, humorous 15–120 word bulletin to
> `.season27-automation/body.txt`. Quote exact games or scorelines where useful. Follow every
> `claim_rules` item: never claim an individual match caused a leaderboard change unless it appears
> in `verified_match_impacts`; describe other matches only as period context. Quote player ranks and
> scores exactly. Avoid insults about protected characteristics, appearance, health, or private
> life. Run `uv run season27-bulletin publish --fact-pack .season27-automation/facts.json --body-file
> .season27-automation/body.txt --output .season27-automation/published.json`, then read the slug and
> run `uv run season27-bulletin verify SLUG`. Report the public path and any failure; do not invent or
> publish fallback facts.

Schedule it weekly for Monday 06:00 in the `Europe/London` timezone. The server pins the reporting
cutoff to the most recent Monday at 06:00, so a slightly late start does not widen the factual period.

## Failure and recovery

- BBC or standings failure returns a non-success response and publishes nothing. Retry after the
  source recovers.
- `facts_changed` means prepare again and regenerate the prose from the new pack.
- `not_configured` means the actor name does not identify an active administrator.
- A failed run may leave no bulletin or a draft; the next run safely resumes a matching draft.
- If Codex was offline at 06:00, run the task manually. The period begins at the previous published
  cutoff, so matches are not lost across missed weeks or month boundaries.
