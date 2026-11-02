-- Autorevert activity per workflow and per triggering signal (misc.autorevert_events_v2).
-- periodic-strict (the @periodic workflow) is where failures auto-revert; pass
-- -p workflow=periodic-strict -p days=90 for its history (it rarely fires), or keep
-- workflow=all to compare every workflow autorevert watches (trunk, pull, inductor, slow, Lint, ...).
-- Actions: restart = rerun jobs to confirm, revert = commit reverted, advisor = advisory signal only.
-- Excludes dry runs and failed actions. Window: events with ts in the last {days} days.
-- Rows with signal_key = '(all signals)' are per-workflow totals; other rows split by
-- source_signal_keys, which mix test keys ("file.py::test") and job keys ("job name [test]").
-- Counts are distinct events; an event naming several workflows/signals counts toward each.
-- @param days=30
-- @param workflow=all
WITH events AS (
    SELECT ts, action, commit_sha, arrayJoin(workflows) AS workflow, source_signal_keys
    FROM misc.autorevert_events_v2
    WHERE ts > now() - INTERVAL {days:UInt32} DAY
      AND repo = 'pytorch/pytorch'
      AND dry_run = 0
      AND failed = 0
      AND ({workflow:String} = 'all' OR has(workflows, {workflow:String}))
)
SELECT
    workflow,
    if(grouping(signal) = 1, '(all signals)', signal) AS signal_key,
    uniqExactIf((ts, commit_sha, source_signal_keys), action = 'revert') AS reverts,
    uniqExactIf((ts, commit_sha, source_signal_keys), action = 'restart') AS restarts,
    uniqExactIf((ts, commit_sha, source_signal_keys), action = 'advisor') AS advisories,
    uniqExactIf(commit_sha, action = 'revert') AS reverted_commits,
    groupUniqArrayIf(5)(commit_sha, action = 'revert') AS sample_reverted_shas,
    max(ts) AS last_event_at
FROM events
LEFT ARRAY JOIN source_signal_keys AS signal
WHERE {workflow:String} = 'all' OR workflow = {workflow:String}
GROUP BY GROUPING SETS ((workflow), (workflow, signal))
ORDER BY grouping(signal) DESC, workflow, reverts DESC, restarts DESC
LIMIT 60
