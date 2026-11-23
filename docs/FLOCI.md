# Amazon Product Intelligence with the existing TestFloci stack

The reference stack is
`C:\Users\sathe\Downloads\TestFloci\ai_product_support_uc1_v11_1_demo_console`.
Its proxy configuration forwards Bedrock Runtime to
`http://host.docker.internal:11434/v1`, with `llama3.1:8b` as the default model.
Use its existing Floci container and persistent volume; no container replacement
or changes to the support application are needed by this integration.

## Run on Windows

1. Start Docker Desktop and the existing Floci container. Check `docker ps -a`
   to find the container, then `docker start floci` if its name is `floci`.
2. Start Ollama with the configuration in the TestFloci runbook. Its listener must
   be reachable from the container; the reference script uses `OLLAMA_HOST=0.0.0.0:11434`.
   Confirm `llama3.1:8b` is installed with `ollama list`.
3. In this project's directory, with dependencies installed, run:

```powershell
.\scripts\run_floci.ps1 -CheckOnly
.\scripts\run_floci.ps1
```

For the four-stage pipeline using the repository's synthetic product fixtures and
real local inference (no mock model outputs), run in the same configured shell:

```powershell
python scripts/check_floci_pipeline.py
```

This checks two candidates and saves evidence to `output/floci_pipeline_check.json`.
It uses a 366-day review window to include the dated test reviews. It does not
validate live Amazon access.

The launcher checks Floci health, directly publishes a CloudWatch metric, and
requests JSON from both configured model aliases through Bedrock `Converse`.
Any failed call exits nonzero. A successful check starts the existing Flask UI
at `http://localhost:5050` unless `-CheckOnly` is supplied.

The model aliases remain the application's Claude/Nova identifiers so its pricing
lookup works. Floci's proxy must map these to the installed local model. These
requests run the configured Ollama model, not hosted Claude or Nova. Reported dollar
amounts are illustrative estimates using AWS price assumptions, not local inference
cost or an AWS bill. The check itself increments the process's estimated budget.

Configuration is also documented in `.env.floci.example`. The client factory uses
dummy credentials in Floci mode, supports `AWS_ENDPOINT_URL` and per-service
overrides such as `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`, and never falls back to AWS
after a local connection failure. Floci mode defaults to requiring successful LLM
calls; review/recommendation errors cannot silently turn into mock summaries.

## What runs locally

| Component | Execution |
|---|---|
| Flask, discovery, ranking, review preparation | Local Python application |
| Product/review collection | Live Amazon via Selenium; Floci does not supply catalogue data |
| Model inference | Application → Floci Bedrock Converse → Ollama |
| Metrics | Application → Floci CloudWatch |
| AgentCore managed runtime | Not established by this local integration |

The TestFloci parity document records AgentCore registry/API emulation without real
runtime execution. Run Flask locally for this path. `agentcore/runtime.py` remains
an AWS deployment entrypoint and requires the AgentCore SDK separately; do not use
an emulator's runtime registration response as proof that the pipeline executed.
The gateway tool handlers and runtime now use the existing Amazon provider, fixing
stale imports of the removed demo provider.

Catalogue access, supported marketplaces, review availability and browser setup are
separate prerequisites. A successful model check does not verify live scraping or
the full recommendation pipeline. Local regression tests use synthetic fixtures
and mocked model responses; they do not establish model quality.

## Verification on 2026-10-15

- Regression suite: 66 tests passed, including evidence retention on model errors,
  explicit computed rankings, bounded upstream-error recovery, browser startup, streaming completion,
  concurrent-run rejection, heartbeat updates and local model failure handling.
- Existing Floci container: health check and CloudWatch publication passed.
- Both model aliases: actual Converse responses through Ollama passed.
- Four-stage fixture pipeline: two synthetic products, actual model-generated
  review summaries and recommendation; evidence in `output/floci_pipeline_check.json`.
- Live Flask pipeline: two Amazon candidates collected and a model-generated
  recommendation returned with an SSE `done` event; evidence in
  `output/floci_live_check.sse`. Neither product yielded recent review text, so
  this run does not validate live review summarization or recommendation quality.

The first live attempt failed because Selenium could not write its driver cache
inside the sandbox. Re-running with access to the driver cache succeeded. If a
new browser version needs a driver download, allow Selenium to update that cache.
The long-running web server must also have that access: a successful one-off check
outside the sandbox does not grant it to a server started inside the sandbox.
After restarting the actual server outside the sandbox, the `bluetooth speaker`
search returned eight live products. Missing-driver errors now surface as browser
setup errors rather than being collapsed into a possible Amazon rate limit.

## Progress and local inference limits

The UI now shows search pages opened, product pages opened, pages processed and
reviews extracted. These count actual browser work, including retries, rather than
assuming each selected product needs a fresh fetch. Discovery prefetches the wider
pool; the review stage reuses those pages. Extracted reviews may lack usable dates
or fall outside the review window; the product cards report the eligible count.

Elapsed-time heartbeats arrive every two seconds while work is pending. Only one
analysis runs at a time, preventing multiple tabs from queuing duplicate inference
jobs on the same model. Local model calls have a 75-second read timeout and no
automatic retries. Review evidence is limited to three excerpts per sentiment,
300 characters each; statistics still use all eligible reviews. These limits are
configurable in `.env.floci.example`. Model failures remain visible, and fatal
errors terminate the stream and stop the UI spinner.

A live `bluetooth speaker` run on marketplace `IN` completed step 4 in approximately
80 seconds: one search page, eight product pages, 62 extracted reviews and five
analyzed candidates. See `output/floci_speaker_IN_progress_check.sse`. No review
dates were usable within the selected window in this run, limiting its evidence.
The `US` search returned no usable listings during the later verification attempt;
it terminated with an explicit error rather than remaining in an analyzing state.

On Windows, ChromeDriver now starts with `close_fds=True` to prevent inheriting the
web server's listening socket. This prevents orphaned automation processes from
retaining port 5050 after a server restart.

The subsequent US failure returned a page titled "Sorry! Something went
wrong!", containing no product tiles or ASIN elements. The app identifies
this upstream error explicitly and offers an optional
button to search India instead. Marketplace switching requires the user's click;
it never substitutes Indian listings for a US search.

On 2026-10-16, a default-Chrome comparison and the previous browser configuration
both returned 16 US search tiles. This demonstrates intermittent upstream behavior;
it does not establish that the old user-agent caused the failure. The app now uses
the installed browser's normal user-agent and image settings, and retries an Amazon
error page once after two seconds in a fresh browser session. Persistent errors and
verification challenges are still reported explicitly. Successful full results are
cached for the day; a cache hit does not require another Amazon scrape.

The interactive UI now preserves measured review statistics if model-generated
summary JSON is invalid or inference fails. The response marks `analysis_source`
as `unavailable`, includes the error, and leaves themes empty. If the final model
explanation fails, the UI returns a ranking from the existing computed scores,
explicitly marked `source=deterministic`, with confidence unestimated. These paths
do not claim successful AI inference. Standalone strict integration checks still
require successful model calls. Cache version v3 avoids reusing older results
that discarded products on summary errors.

The final 2026-10-16 US verification completed with five analyzed candidates and a
model-generated recommendation in approximately 48 seconds, using one search page
and eight product pages. Evidence: `output/floci_US_complete_2026-10-16.sse`.
That particular run exposed no review text or prices, so it confirms pipeline
completion rather than comprehensive evidence or recommendation quality.
