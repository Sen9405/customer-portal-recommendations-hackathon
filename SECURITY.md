# Security and data handling

The closed review server uses the organizer's pseudonymized dataset. Run it on loopback with `CABINET_ONLY=1` and expose it only through a trusted, access-controlled gateway. The browser token is a same-origin request check, not authentication. Do not use the closed server as a public multi-user service.

- The contest dataset, its row-level derivatives, local notes, official baseline file, credentials and backups stay outside the public repository. A restricted live pitch, a downloadable dataset and a shareable URL are different permissions.
- For independent evaluation on an unauthenticated URL, use fictional purchase histories with public product information, or a reviewed export explicitly approved for that audience by the organizer. Keep aggregate metrics from the real evaluation separate and label them accordingly. The built-in synthetic demo is a small smoke-test, not the full agentic contest UI.
- Use an unprivileged account, `umask 077`, private directories (`0700`) and private files (`0600`). Use a server-side credential store for DeepSeek. Do not send purchase records, customer identifiers or their derivatives to model providers from any public deployment.
- `OFFER_AGENT_ADAPTER` points to trusted Python code, not user input. Offline comparison scripts that accept an organizer baseline **execute Python**; do not run unknown copies, or run them on a host with secrets. For untrusted code use a separate disposable VM/container, network disabled, with only the required read-only inputs mounted. Dependency hashes do not validate these local scripts or the authenticity of the incoming dataset.
- Before deployment, verify authentication (if needed), TLS, bounded access/model usage, and the published endpoint from outside the host. Keep direct bind ports private. A private URL by itself is not an access control.

Check a proposed staged public release with `python3 tools/check_release.py`. Export a reviewed subset with `python3 tools/check_release.py --export /absolute/new/directory`. The gate checks paths/types and common secret patterns; it does not replace human review of new files or rights to redistribute them.
