# CLAUDE.md

This file provides context and guidance for AI assistants (like Claude) working in this repository.

## Repository Overview

This is a **GitHub profile repository** for [@BlockchainAttorney](https://github.com/BlockchainAttorney). GitHub treats a repository named after the account owner as special — its `README.md` is automatically displayed on the user's public GitHub profile page.

- **Owner:** BlockchainAttorney (Alexandru)
- **Contact:** alexandru@blockchainlegal.tech
- **Focus areas:** Blockchain technology, Solidity, NEVM, Syscoin

## Repository Structure

```
BlockchainAttorney/
└── README.md    # GitHub profile page content (rendered at github.com/BlockchainAttorney)
```

There is no application code, build system, test suite, or dependency management in this repository.

## Development Workflow

### Branch Strategy

- `main` / `master` — production branch; content here appears on the live GitHub profile
- Feature branches follow the pattern `claude/<description>-<id>` for AI-assisted changes

### Making Changes

1. Edit `README.md` to update the profile content
2. Commit with a descriptive message
3. Push to the target branch and open a PR if needed

### Git Conventions

```bash
# Commit message style (imperative, present tense)
git commit -m "Update profile to reflect new interests"
git commit -m "Add links to recent projects"

# Push to feature branch
git push -u origin <branch-name>
```

## README.md Conventions

The `README.md` uses GitHub Flavored Markdown and is rendered directly on the profile page. Keep in mind:

- GitHub renders emoji shortcodes (e.g., `👋`, `👀`, `🌱`) — use them sparingly and purposefully
- The HTML comment block at the bottom (`<!-- ... -->`) is a GitHub-inserted note; it does not render publicly
- Keep the content concise — profile READMEs are meant to be a quick introduction
- Avoid adding raw HTML unless needed for layout (e.g., centering images, badges)

## Common Profile README Enhancements

If asked to improve or extend this profile, consider these common patterns:

- **GitHub stats badges** — show contribution graphs, streak stats, top languages (via services like `github-readme-stats`)
- **Skills/tech stack section** — list languages, tools, and platforms using badge icons (shields.io)
- **Current projects section** — link to active or noteworthy repos
- **Social links** — LinkedIn, Twitter/X, personal website
- **Blog or writing** — link to articles or publications

## Key Reminders for AI Assistants

- This repo has **no code to test, build, or lint** — skip any tool-related steps
- All meaningful content lives in `README.md`
- Changes here are **publicly visible** on the GitHub profile — keep content professional and accurate
- Do not add application scaffolding, configuration files, or tooling unless explicitly requested
- When committing, push to the designated feature branch and do not push directly to `main`/`master` without instruction
