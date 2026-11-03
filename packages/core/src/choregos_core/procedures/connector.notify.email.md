---
id: connector.notify.email
title: Connect an SMTP relay for e-mail notifications
applies_to: {connector: notify/email}
audience: project_owner
version: 1
reviewed: 2026-11-03
vendor_docs:
  - {title: "SMTP submission (RFC 6409)", url: "https://www.rfc-editor.org/rfc/rfc6409"}
egress: []
values: []
fields:
  host: "the SMTP server's host name, for example smtp.example.com"
  port: "the SMTP port: 587 (STARTTLS) or 465 (implicit TLS)"
  username: "the SMTP account name, empty for a relay that needs no login"
  from_address: "the sending address, for example choregos@example.com"
  to: "default recipients, used when the project sets no e-mail addresses"
  starttls: "upgrade the connection with STARTTLS (port 587)"
  use_ssl: "use implicit TLS from the first byte (port 465)"
  password: "the SMTP account's password"
---

## Before you start

- An SMTP relay you may send through: your mail provider's submission service, or your company's relay.
- Its host name and port, and whether it uses STARTTLS (usually port 587) or implicit TLS (port 465).
- A sending address the relay accepts, and the account's password if it requires a login.

## At your mail provider

1. Create an account or an app password dedicated to Choregos, so you can revoke it alone.
2. Note the host, the port and the TLS mode.
3. Check that the sending address is allowed for that account.

## Permissions

- The account only needs to send mail. Give it no mailbox access.
- The password is a write-only secret: Choregos seals it and never shows it again.

## In Choregos

1. Project settings, Connectors, **Notify**, type **E-mail (SMTP)**.
2. `host`, `port` and the TLS mode: keep `starttls` on for port 587, or turn on `use_ssl` for port 465.
3. `from_address`: the sending address.
4. `username`, and the `password` in its password field (sealed, shown once).
5. Recipients come from the project's e-mail addresses (Project settings, Notifications). `to` on the
   connector is the fallback when the project sets none.
6. Open the network policy: the platform reaches your SMTP host on the port you chose. SMTP does not go
   through the HTTP egress guard, so the operator's network policy must allow that host and port.

## Test it

Trigger a notification, for example a release train departure, and look in the recipients' inbox. The
subject starts with `[Choregos]`.

## When it fails

- `no recipient`: set e-mail addresses on the project, or `to` on the connector.
- A timeout or `connection refused`: the network policy does not open the host and port.
- `authentication failed`: the username or password is wrong; paste the password again.
- `STARTTLS extension not supported`: the port speaks implicit TLS; turn on `use_ssl` and turn off `starttls`.
- Sender refused: the relay does not allow `from_address` for this account.
