# TradingAgents UI

A Streamlit front end for [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
— the multi-agent LLM trading framework. Pick a ticker and a date in the browser
and watch the analyst, researcher, trader and risk agents work their way to a
Buy / Overweight / Hold / Underweight / Sell rating, instead of driving the
terminal CLI.

![pages](https://img.shields.io/badge/streamlit-app-0f8f5f)

## Quick start

```bash
./run.sh                 # creates .venv, installs everything, opens the app
```

or, manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # installs streamlit + tradingagents
cp .env.example .env                     # then paste your API keys into .env
streamlit run Home.py
```

The app opens on <http://localhost:8501>.

## Keys

The framework needs one LLM key for the provider you pick, plus a data-vendor key
if you move off the yfinance default:

| What | Env var |
| --- | --- |
| OpenAI / Anthropic / Google / DeepSeek / … | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, … |
| Alpha Vantage data vendor | `ALPHA_VANTAGE_API_KEY` |
| Macro indicators | `FRED_API_KEY` |

Set them in `.env`, in your shell, in Streamlit secrets — or type them on the
**Settings → API keys** tab, where they stay in the running process for the
session and are never written to disk.

Ollama and other local endpoints need no key at all: pick the provider on the
Settings page and point the backend URL at your server.

## The pages

| Page | What it does |
| --- | --- |
| **Home** | Setup checks, a quick-start form, recent runs |
| **New Analysis** | Ticker, date, analyst team and depth; then a live view of every agent as it reports, with the reports rendered as they are written |
| **Run History** | Every saved run's markdown, per agent or as one document, with downloads |
| **Settings** | Provider, deep/quick models, reasoning level, endpoint, keys, debate rounds, report language, data vendors, temperature, token caps, checkpointing, results directory |

Settings persist to `~/.tradingagents/ui_settings.json`. Reports are written to
`~/.tradingagents/logs/reports/<TICKER>_<timestamp>/` unless you change the
results directory.

## How it works

`ta_ui/engine.py` runs the framework's LangGraph pipeline on a background
thread, following the same sequence as the upstream CLI — resolve the
instrument identity, build the initial state, stream the graph, merge the
streamed deltas, then extract the rating and write the report tree. The thread
pushes events onto a queue that the Streamlit page drains, so a run survives the
page reruns Streamlit does on every interaction.

```
ta_ui/
  catalog.py    providers, models and analysts (read from the framework when installed)
  settings.py   persisted preferences, secrets handling, config assembly
  engine.py     the background run: streaming, activity log, reports
  sections.py   which state fields map to which report card, markdown export
  ui.py         shared Streamlit chrome
```

## Notes

- A four-analyst run makes dozens of model calls; the deep-thinking model does
  most of the spending. Trim the analyst team or the debate rounds to cut cost.
- Keep the browser tab open during a run — the analysis lives in the server
  process, and closing the last tab eventually reclaims the session.
- Research software, not investment advice.
