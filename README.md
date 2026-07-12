# Sift

A generic signal-detection engine. Point it at a domain, and it surfaces
the posts that matter, ranked by relevance.

Sift watches a set of sources (currently Reddit, via RSS), scores every
new post against a rubric using an LLM, and prints the top results. The
domain and rubric live entirely in a config file, never in the code -
swap the config and Sift watches something completely different.

## How it works

1. Pull recent posts from each subreddit listed in the config (via RSS,
   no API keys or app registration required)
2. Score each post against the config's rubric using Claude
3. Sort by score, print the top N

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set your Anthropic API key as an environment variable:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

(or copy `.env.example` to `.env`, fill it in, and load it with
`python-dotenv` if you prefer)

## Usage

```bash
python sift.py --config config.json
python sift.py --config config.json --top 10
```

## Configuring a new domain

Edit `config.json`, or create a new one and point `--config` at it:

```json
{
  "sources": ["reddit"],
  "communities": ["ArgentinaFootball", "soccer"],
  "domain": "Argentina national team match commentary",
  "rubric": "Score 0-100. Classify as 'breaking' or 'reaction'.",
  "post_limit_per_community": 25
}
```

No changes to `sift.py` are required to point it at a new domain.

## Status

This is v0: a single script, Reddit only, no persistence, no scheduling,
no push notifications. It exists to answer one question - does the
rubric produce a ranking worth trusting? Everything after that is
infrastructure.
