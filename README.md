# autofeeder

Automatically aggregate, sort by relevance, and summarize journal and news feeds. Then spoon feed you the good bits.

autofeeder is a CLI tool that scores RSS items against your interests using a two-pass LLM pipeline, extracts full content where possible, and generates tiered digests. It supports multiple interest profiles, multiple LLM backends, and multiple output formats.

## Features

- **Two-pass LLM pipeline** -- triage scoring across all items, then per-item summarization for those that make the cut
- **TL;DR overview** -- a ~12-sentence briefing of the week's highlights, generated from scored results
- **Multiple interest profiles** -- neuro research, Alzheimer's, gaming hardware, whatever you care about
- **Feed discovery** -- `--discover "your topic"` generates a starter profile with RSS feeds, keywords, and narrative
- **Content extraction** -- Unpaywall (academic open access) -> PubMed Central -> direct fetch -> archive.ph
- **PubMed enrichment** -- abstracts, MeSH terms, related articles
- **"Builds on your work" detector** -- flags papers citing your methods or tools
- **Output plugins** -- Markdown, Slack (Block Kit), Obsidian vault, HTML email
- **Tiered digest** -- TL;DR -> Builds on your work -> Top picks -> Also relevant
- **Dedup ledger** -- tracks seen items across runs, highlights NEW papers, supports `--diff-only`
- **Feed health monitoring** -- detects dead, broken, or empty feeds
- **Cross-profile Paper of the Week** -- surfaces the single best paper across all profiles
- **Backends** -- Anthropic (direct API + Bedrock), OpenAI, local LLM (LM Studio / Ollama)

## Quickstart

```bash
git clone https://github.com/jinchiwei/autofeeder.git && cd autofeeder
pip install -e .
python autofeeder.py --setup
```

The setup wizard walks you through API key configuration, validates your connection, and optionally creates your first profile. The whole process takes about 2 minutes.

After setup:

```bash
python autofeeder.py --discover "your research topic"   # create a profile
python autofeeder.py --profile your-topic               # run it
```

## Configuration

Three layers, each overriding the last:

1. **`config.toml`** -- global defaults: backend, fetch limits, triage batch size, output thresholds, model selection
2. **`profiles/*.toml`** -- per-profile feeds, interests, keywords, narrative, `[my_work]` for citation detection, output destinations (Slack, Obsidian, email), and optional overrides for any global setting
3. **`.env`** -- API keys and secrets. Environment variables can also override config values (`LOOKBACK_DAYS`, `MIN_SCORE`, etc.)

Copy `profiles/example.toml` to get started. The example shows the full schema: feeds, keywords, narrative, paywalled domains, your publications, and output config.

See [DESIGN.md](DESIGN.md) for output styling, typography hierarchy, and tone guidelines.

## Usage

```bash
python autofeeder.py --profile neuro              # Run one profile
python autofeeder.py --all                         # Run all profiles
python autofeeder.py --diff-only --profile neuro   # Only new items since last run
python autofeeder.py --discover "topic"            # Generate a starter profile with feeds
```

## Output format

Digests are tiered by relevance:

1. **TL;DR** -- a short paragraph summarizing the week: what was scanned, what stood out, key themes
2. **Builds on your work** -- papers that cite your tools or methods (only appears if matches are found)
3. **Top picks** -- highest-scoring items with full summaries, key takeaways, and relevance explanations
4. **Also relevant** -- items above the score threshold but below top-pick territory

Each item includes a relevance score, source metadata, publication date, a one-line headline, bullet takeaways, a "why this matters" paragraph, and tags.

## License

[MIT](LICENSE) -- Jinchi Wei, 2026
