---
id: connector.documents.web
title: Connect a web site behind a login as a documents source
applies_to: {connector: documents/web}
audience: org_admin
version: 1
reviewed: 2026-10-28
vendor_docs:
  - {title: "HTTP authentication (MDN)", url: "https://developer.mozilla.org/en-US/docs/Web/HTTP/Authentication"}
egress: ["the site's host"]
fields:
  host: "the site's host name, docs.example.com; the credential is sent to no other host"
  auth: "bearer for a token, basic for a user name and password, header for a secret in a header of its own"
  username: "auth basic: the user name"
  header_name: "auth header: the header the secret goes in, X-API-Key or Cookie"
  headers: "plain headers sent with every request, Accept-Language for example; never a credential"
  secret: "the token, the password or the header's value, typed once"
---

## Before you start

- A web site whose pages a knowledge base should read, but that answers only to a signed-in reader: an
  intranet published on the internet, a documentation portal, a wiki with an API token. A public site
  needs no connector: a web source reads it anonymously.
- This is an organisation connector: an organisation administrator declares it once. The credential is
  **pinned to its host**: a source whose address is on another host is refused, and a crawl never leaves
  its start address's scheme and host, so the credential is never sent elsewhere. Declare one connector
  per host.
- The host must still resolve to a public address: a site on a private network is refused whatever the
  credential.

## At the vendor

Ask the site's owner for a credential **made for an automated reader**, with read access only:

- **Bearer**: an API or access token the site accepts as `Authorization: Bearer <token>`.
- **Basic**: a service account's user name and password, sent as HTTP Basic.
- **Header**: a secret the site expects in a header of its own, `X-API-Key: <key>`, or a long-lived
  session cookie (`Cookie: name=value`). A session cookie expires: note when, and plan its renewal.

A login form that needs a browser, a second factor or a single sign-on redirect cannot be crawled this
way: ask for a token, or use the site's repository or another connector.

## Permissions

- Read access to the pages to index, nothing else. The crawl only sends GET requests.
- The secret is kept in the platform's secret store and resolved when the crawl starts; it is never shown
  again, written in a log line or returned by the API.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Documents**, type **Web site behind a login**.
2. `host`: the site's host name, without `https://`.
3. `auth`: `bearer`, `basic` (then `username`) or `header` (then `header_name`). Paste the token, password
   or header value in `secret`.
4. Optionally `headers`: plain headers sent with every request, such as `Accept-Language`.
5. **Knowledge**, a base, **Add source**, kind **Web**, open **Advanced: authentication and HTTP headers**
   and pick this connector under **Authentication**.

## Test it

Add the source: the platform asks for the start address with the credential and refuses a start that
redirects elsewhere (usually to a login page). Then press **Sync now** and open the documents that were
added.

## When it fails

- "is not on `<host>`": the source's address is on another host than the connector's `host`, by design.
- "redirects to … start there": the site sent the request to its login page; the credential is wrong,
  expired, or not the kind the site accepts.
- Nothing indexed: `robots.txt` disallows the paths, or the pages answer `401`/`403` to this credential.
- "resolves to a private address": not crawled, whatever the credential.
