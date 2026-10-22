# Security

Pettagam is a personal tool that runs as you on your own computer. What it protects, and how, is set out in [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) (sections 6.1 Data safety, 6.2 Security and 6.3 Privacy).

In short:

- It only reads the old files, never replaces a file in the library, and removes a file from the inbox only after its copy is safely on disk.
- The panel listens on 127.0.0.1 and answers only requests addressed to this computer. Network mode (`--host 0.0.0.0`) needs a random token, but uses plain HTTP; prefer an SSH tunnel.
- Files are sent to Anthropic only for AI tagging, which you install, give a key and confirm. Pettagam stores no keys or passwords.

## Reporting a problem

If you find a way Pettagam could lose or change a file, or let someone else use the panel, please open a [private security advisory](https://github.com/javajaga-usa/Pettagam/security/advisories/new) rather than a public issue. Include what you ran and what happened. Only the latest release is supported.
