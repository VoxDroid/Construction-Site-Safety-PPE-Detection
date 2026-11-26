# Security

The dashboard shows live video of people and lets anyone who can open it change settings, so
protect it before making it reachable from other machines.

## The default: local only, no password

`ppe-guard serve` listens on `127.0.0.1`, which only the same computer can reach. With no password
set, everyone who can reach the dashboard can use it. That is fine on your own laptop.

## Set a password

Either in the dashboard, **Settings → Security**, or from a terminal:

```bash
ppe-guard password set          # asks for the password twice
ppe-guard serve --host 0.0.0.0  # now reachable from your network
```

or with an environment variable, which is handy for Docker:

```bash
PPE_GUARD_PASSWORD="a long passphrase" ppe-guard serve --host 0.0.0.0
```

The environment variable is applied at every start and replaces a password set in the dashboard.

With a password set:

- Every page, API call, video stream, snapshot, clip and the WebSocket require a login. The only
  public addresses are the login page and its API, the static files (styles and scripts, which
  hold no data) and `/api/health`.
- Logging in gives a session cookie (`HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS) valid for
  12 hours (or until the browser closes), or 30 days with *Keep me signed in*.
- After 10 wrong passwords from one address in 10 minutes, logins from it are refused for a while.
- Changing the password signs out every other session.

Passwords must be at least 8 characters. They are stored as salted scrypt hashes in
`auth.json` in the data folder, next to the session signing key and the API token's hash. Keep
the data folder private.

To remove the password, use **Remove password** in **Settings → Security** or
`ppe-guard password clear`.

## API tokens

Scripts and other systems can use an API token instead of the password. Create one in
**Settings → Security** or with `ppe-guard password token`. It is shown once.

```bash
curl -H "Authorization: Bearer ppe_XXXXXXXX" http://site-server:8000/api/stats
```

For tools that can only take a URL, such as a video wall or a home automation camera entity, add
it as a query parameter. This works for GET requests only:

```text
http://site-server:8000/api/cameras/gate/stream?token=ppe_XXXXXXXX
```

A token can read and change data, but it cannot change the password or create tokens. There is
one token at a time; creating a new one replaces the old one.

## Use HTTPS

The password and session cookie travel in clear text over plain HTTP. Anywhere beyond a trusted
local network, put the dashboard behind a reverse proxy with HTTPS, or reach it over a VPN. See
[Running in production](../deployment/production.md).

## Privacy

PPE Guard records video of people at work. Before installing cameras, check what your local law
and your workplace agreements require (for example notices on site, a data protection impact
assessment, or worker consultation), and set a [retention period](violations.md#keeping-storage-under-control)
so footage isn't kept longer than needed.

PPE Guard does not recognise faces or identify people, and sends nothing outside your network
unless you configure a webhook.

## Reporting a vulnerability

Please report security problems privately through GitHub's
[security advisories](https://github.com/VoxDroid/ppe-guard/security/advisories/new) rather than
in a public issue.
