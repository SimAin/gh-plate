# Security

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Use GitHub's
private reporting instead: **Security → Report a vulnerability** on this
repository, or
<https://github.com/SimAin/gh-plate/security/advisories/new>.

You should hear back within a week. If the report is accepted, a fix will be
released and credited to you in the advisory unless you ask otherwise.

## Scope

`plate` is a read-only reporting tool. It holds no credentials of its own —
every GitHub call goes through the `gh` CLI and its existing authentication —
and it has no runtime dependencies beyond the Python standard library. Things
that would count as vulnerabilities here:

- anything that could exfiltrate or log `gh`'s token or your private data;
- command injection through issue/PR/label text, repository names, or config;
- writing to, or otherwise mutating, anything on GitHub.

## Supported versions

Only the latest release receives fixes.
