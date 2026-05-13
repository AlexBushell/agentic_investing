# Company Intelligence Platform

This repository is the starting point for a local-first company intelligence store and framework runner, with the first working slice focused on acquiring annual reports from the FCA National Storage Mechanism and using them to support an IVF pre-screen.

## Current Focus

The first active vertical slice is:

- Playwright-based NSM browser automation
- annual report acquisition and storage
- annual-report-led IVF pre-screen scaffolding

The roadmap for that slice lives in [IVF_PreScreen_First_Slice_Roadmap.md](C:\dev\agentic investing\IVF_PreScreen_First_Slice_Roadmap.md).

## Local Setup

Use one repo-level virtual environment for the whole project. At this stage, the acquisition, parsing, storage, and framework runner pieces are part of one application, so a single `.venv` keeps setup and dependency management much simpler.

1. Create and activate a virtual environment in the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install the project in editable mode:

```powershell
pip install -e .
```

3. Install Playwright browsers:

```powershell
python -m playwright install
```

4. Copy `.env.example` to `.env` and fill in the database and API settings.

```powershell
Copy-Item .env.example .env
```

5. Confirm the CLI entrypoint works:

```powershell
research list-frameworks
```

## Environment Strategy

For now, use:

- one repo
- one `.venv`
- one `pyproject.toml`

Do not create separate virtual environments per subsystem unless the project later splits into genuinely independent services. If we need separation later, prefer dependency groups or optional extras before introducing multiple environments.

## Useful Commands

List the registered frameworks:

```powershell
research list-frameworks
```

Show the configured database target:

```powershell
research init-db
```

Run the NSM downloader scaffold in a visible browser:

```powershell
research ingest-nsm-report --query "Company Name" --headed
```

The NSM downloader captures debug artifacts as it works through the live FCA flow. If the site markup changes, update [config/nsm.yaml](C:\dev\agentic investing\config\nsm.yaml) rather than editing `.env`.

## Configuration

Keep deployment-specific values in `.env`, such as:

- `DATABASE_URL`
- `LLM_PROVIDER`
- `LLM_MODEL`
- `OPENROUTER_API_KEY`
- `BROWSER_CHANNEL`

Keep NSM site behavior and selectors in [config/nsm.yaml](C:\dev\agentic investing\config\nsm.yaml). That file is versioned with the repo because it describes how our downloader interacts with a specific source, and we want changes to those selectors and timings to be visible in git history.

Keep framework-runner behavior in [config/framework_runner.yaml](C:\dev\agentic investing\config\framework_runner.yaml). That includes framework-specific knobs such as the IVF pre-screen temperature and repair policy, which are better treated as versioned application behavior than deployment secrets.
