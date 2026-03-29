# autofeeder — Design Language

Internal design reference. Not public — guides all output formats.

## Fonts

- **Body text:** Geist
- **Scores, tags, metadata:** Geist Mono
- **Fallback stack:** -apple-system, system-ui, sans-serif / monospace

## Color Roles

| Color | Hex | Role |
|-------|-----|------|
| Turquoise | `#40E0D0` | Primary accent — links, score highlights, section headers |
| Deeppink | `#FF1493` | High-signal callouts — "builds on your work" badge, alerts. Use sparingly; rarity = impact |
| Gold | `#FFD700` | Achievement/distinction — Paper of the Week badge, top score highlights |
| Blueviolet | `#8A2BE2` | Structural — section dividers, tags, secondary accents |
| Dark bg | `#1a1a2e` | Email background |
| Light text | `#e8e8e8` | Email body text |
| White | `#ffffff` | Markdown (inherits reader theme) |

## Tone of Voice

- **Concise and direct.** "5 papers worth your time" not "We've curated a selection of papers for your consideration."
- **Data as hook.** Lead with numbers: "12 scanned, 5 relevant, 2 cite your work."
- **Smart colleague, not newsletter.** The feel of a brief from someone who read everything so you don't have to.
- **No hype, no filler.** Never "exciting new research" — just state what they found.

## Emoji Usage

Deliberate, not decorative. Each emoji has one meaning:

| Emoji | Meaning | Where |
|-------|---------|-------|
| 🔬 | Cites your work / uses your methods | Paper badge |
| ✨ | New since last run | Paper badge |
| 📄 | Full text available | Content source |
| ⚠️ | Summary only (paywalled) | Content source |
| 🏆 | Paper of the Week | Cross-profile highlight |

No other emoji in outputs. If it's not in this table, don't use it.

## Typography Hierarchy (Markdown)

```
# Digest title (H1 — one per digest)
  ## TL;DR (H2)
  ## Section headers: "Builds on your work", "Top picks", "Also relevant" (H2)
    ### Paper titles (H3, linked)
      > Headline (blockquote — the one-liner summary)
      **Score: 0.XX** · *Source* · Published: date
      - Key takeaway bullets
      **Why this matters:** relevance paragraph
      Tags: `tag1` `tag2` (backtick-formatted)
      <details> for full text
```

## Slack Formatting

- Header: `autofeeder · {profile} · {date} — {N} papers scanned, {M} worth your time`
- Body: TL;DR paragraph → top 5 papers (title linked, score, one-line headline)
- Footer: "View full digest →" link
- Keep it tight — Slack is a notification, not the digest

## Email Design Direction

- **Minimal chrome.** No banner images, no logo, no heavy header
- **Personal briefing feel.** Like an email from a smart colleague, not a SaaS newsletter
- **Dark theme** (dark bg, light text) — distinctive, easy on eyes
- Geist Mono for scores and metadata
- Turquoise links, deeppink for "cites your work" only, gold for Paper of the Week
- Max width 600px (email standard)

## Obsidian File Design

- YAML frontmatter: structured for Dataview queries (score, tags, date, source, profile)
- Body: headline blockquote → takeaways → relevance → collapsed full text
- Filename: `{date}-{slug}.md` — sorts chronologically in file explorer
- Tags in frontmatter use Obsidian `#tag` format for graph view integration
