# Security policy

## Supported versions

Only the latest release on the `main` branch receives fixes.

## Reporting a vulnerability

Please **do not open a public issue** for anything you believe is a security problem.

Use GitHub's private vulnerability reporting instead:
**https://github.com/JeremiahM37/action-receipt/security/advisories/new**

That opens a private advisory that only the maintainer can see. Include what you found, how to
reproduce it, and what you think the impact is. You will get an acknowledgement within a few
days; a fix, a coordinated disclosure date and credit in the advisory follow from there.

## Scope notes

`action-receipt` drives a browser on behalf of an agent. Things that are in scope:

- a receipt that can be made to claim an effect that did not happen (or hide one that did) by
  content the page controls;
- a page that can crash, hang or escape the MCP server process (the server must never raise past
  a tool call, and settlement must always terminate);
- secrets leaking into receipts (password values are masked; anything else that leaks is a bug).

Things that are **not** vulnerabilities in this project: what an agent chooses to do with the
receipt, and the security of the sites it is pointed at.
