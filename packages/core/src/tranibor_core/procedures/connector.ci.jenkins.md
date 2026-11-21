---
id: connector.ci.jenkins
title: Trigger and follow builds on Jenkins
applies_to: {connector: ci/jenkins}
audience: project_owner
version: 1
reviewed: 2026-11-20
vendor_docs:
  - {title: "Jenkins remote access API", url: "https://www.jenkins.io/doc/book/using/remote-access-api/"}
  - {title: "Jenkins CSRF protection", url: "https://www.jenkins.io/doc/book/security/csrf-protection/"}
  - {title: "Jenkins Notification plugin", url: "https://plugins.jenkins.io/notification/"}
  - {title: "Jenkins multibranch pipelines", url: "https://www.jenkins.io/doc/book/pipeline/multibranch/"}
egress: []
values: ["webhook_url:jenkins"]
fields:
  base_url: "your Jenkins controller, for example https://jenkins.example.com"
  job: "the job's path (folder/app), or the multibranch folder"
  user: "the Jenkins user the API token belongs to"
  multibranch: "true when `job` is a multibranch pipeline, built branch by branch"
  ref_parameter: "the build parameter that receives the branch or tag (BRANCH by default)"
  parameters: "build parameters sent with every trigger"
  crumb: "true to ask for a CSRF crumb before a trigger (the default)"
  token: "the user's API token, typed once"
---

## Before you start

- A Jenkins controller the platform can reach, and a job that builds the project. A plain job should be
  **parameterised** (a string parameter for the branch); a multibranch job is built on the branch itself.
- A Jenkins user for the platform, allowed to build and read that job (`Job/Build`, `Job/Read`).
- For notifications, the platform's shared webhook secret (`TRANIBOR_GENERIC_WEBHOOK_SECRET`), held by whoever
  operates Tranibor.

## At Jenkins

1. Create a user for the platform, then **Configure, API Token, Add new Token**. Copy it when it is shown.
2. Optional, to be told when a build starts and ends instead of polling: install the **Notification plugin** and
   add the endpoint `{{webhook_url:jenkins}}?token=<the shared webhook secret>` (format JSON, protocol HTTP,
   events Job Started and Job Completed). A pipeline can instead post `{"job", "build_number", "result",
   "sha", "branch", "project", "work_item"}` to that URL with the `X-Choregos-Secret` header.
3. Name the commit in the build so the platform can find a commit's build: a pipeline that checks out a
   revision records it (the SCM revision is read), or pass the commit as a build parameter.

## Permissions

- The user needs `Overall/Read`, `Job/Read` and `Job/Build` on the job, and nothing else.
- An API token needs no CSRF crumb on a recent Jenkins; the connector still asks for one and goes without
  when the controller has none (`crumb: false` skips the request).

## In Tranibor

1. Project settings, Connectors, **CI**, type **Jenkins**.
2. `base_url`, `job` (for example `team/app`), `user`. Tick `multibranch` for a multibranch folder: the
   branch is then the last segment of the job path.
3. `token`: the API token, in the password field (sealed, shown once).

## Test it

Press **Test**: the connector reads the job and answers `ok`. Then trigger a build and watch it leave the
queue and finish.

## When it fails

- `401` or `403`: the user or token is wrong, or the user may not build the job. A `403` mentioning the crumb:
  the controller wants a crumb and `crumb` is off.
- `404` on the job: check the path; a folder is `folder/app`, never `/job/folder/job/app`.
- A trigger answered `400` or `500`: the job is not parameterised and `ref_parameter` is set, or a required
  parameter is missing from `parameters`.
- Status stays `unknown`: no recent build recorded the commit. Only the last builds of the job are read.
- Notifications ignored with `401`: the token in the URL is not the platform's shared webhook secret.
