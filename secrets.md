# Secrets Audit

This file records potential secrets detected during repository
productionalization.

No secret values are stored in this report.

Audit performed: 2026-09-12 (pattern-based scan over `*.py`, `*.js`, `*.jsx`,
`*.json`, `*.txt`, `*.env*`, `*.tsv`, `*.csv`, `*.md`; checked for API keys,
tokens, passwords, private keys, PEM blocks, connection strings, `.env` files).

| File | Line | Secret Type | Action | Status |
|---|---:|---|---|---|
| `original-project/get_all_genes.py` | — | CLI `--api-key` placeholder (`YOUR_NCBI_KEY` docstring example) | None — no real value present | No action needed |
| `original-project/query_gene_indels.py` | — | CLI `--api-key` placeholder (`YOUR_NCBI_KEY` docstring example) | None — no real value present | No action needed |

## Summary

- No plaintext credentials, tokens, private keys, or connection strings found.
- No `.env` file is committed; `.env` and `.env.*` are gitignored
  (`.env.example` is whitelisted and contains placeholders only).
- Backend configuration is environment-variable driven (`backend/app/config.py`).
