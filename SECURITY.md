# Security Policy

## Supported Versions

None yet. Friday Agent OS has not made any release. There is no supported
version until the project reaches an initial release milestone.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately rather than opening a
public issue. While this repository is private, report through the hosting
platform's private channel to the repository owner (e.g. a private security
advisory or a direct message) rather than a public issue or PR.

*(Repository owner: once this repository is public, replace this section
with a monitored security contact — e.g. an email address or a hosting
platform vulnerability-reporting feature.)*

Include what you found, steps to reproduce, and potential impact. Do not
include real credentials, tokens, or sensitive data in a report.

## Handling Sensitive Data

- Never commit `.env` files, API keys, OAuth tokens, session files, local
  databases, or generated runtime artifacts to this repository.
- If a secret is accidentally committed, treat it as compromised: rotate it
  immediately and remove it from history.

## Current Security Model

The security model for Friday Agent OS is still under design. As of Phase 0,
there is no runtime, authentication, authorization, or tool execution surface
to secure — this repository contains only the repository foundation.

Future phases that introduce tool execution or computer-use capability will
require explicit policy and approval controls (e.g. scoped permissions,
human-in-the-loop confirmation for risky actions) before such capabilities
are enabled. Those controls are not yet designed or implemented.
