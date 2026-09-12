# Security Policy

## Reporting a vulnerability

If you find a security issue in this project, please **do not open a public
issue**. Instead, report it privately via GitHub's
[private vulnerability reporting](https://github.com/thecyberthriver/nyc-free-events/security/advisories/new)
(Security tab → Report a vulnerability). I'll respond as soon as I can.

## Secrets

This project holds **no secrets in source control**. All credentials
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`) are supplied at
runtime via environment variables — GitHub Actions Secrets in the cloud, or a
git-ignored `secrets_local.py` when running locally. If you believe a secret was
ever committed, treat it as compromised and rotate it (BotFather `/revoke` for
the Telegram token; the Anthropic console for the API key).

## Automated checks

- Secret scanning + push protection (enabled when the repo is public)
- Dependabot alerts and security updates
- Least-privilege `GITHUB_TOKEN` (read-only) and SHA-pinned Actions
