# Volusia County Homeschool Directory — Development & Hosting Plan

**Status:** Draft, 14 September 2026
**Developer:** Jason Barnes
**Content manager:** Spouse (non-technical, weekly editing)

---

## What this is, and what it isn't

This is not a content management system project. It is a **structured records project with a public reading surface** — a few hundred homeschool programs in one Florida county, each with a name, a category, a contact, a schedule, and a link. That distinction drives every decision below. Records are what the Django admin was built for, and it is why we are not reaching for Wagtail, a headless CMS, or a static site generator with a browser-based editor bolted on.

The success condition is not launch day. It is that eighteen months from now the directory is still accurate, because adding a program takes her four minutes and nobody has to ask you to do it.

**Out of scope for v1:** user accounts for program owners, reviews or ratings, a map view, paid listings, an events calendar, email newsletters. Several of these are reasonable v2 candidates and are noted at the end.

---

## Stack

- **Django 5.2 LTS**, security-supported through April 2028. Django 6.x is current, but the LTS is the right call for a site you want to ignore for two years at a stretch. Python 3.12, which is what Ubuntu 24.04 ships.
- **SQLite in WAL mode.** At a few hundred rows with one writer, this is not a compromise — it is faster than Postgres over a socket and eliminates an entire service from the maintenance surface.
- **Gunicorn** behind **Caddy**, which handles TLS certificates automatically with no certbot cron to forget about.
- **WhiteNoise** for static files, so there is no separate static-file server to configure.
- **django-unfold** for admin theming. This is the single highest-leverage dependency in the project.
- **django-prose-editor** for rich text, with a deliberately narrow toolbar and server-side sanitization via `nh3`.
- **Litestream** streaming the SQLite WAL to Backblaze B2 for continuous backup.

Everything above runs on one small server. Whether it runs as system services or as containers is a deploy-method choice rather than an architecture choice — see *If you'd rather run containers* below. What we are not doing at this scale is orchestration or a CI/CD platform, because each adds more failure modes than it removes.

---

## Data model

The shape of the model is where the admin experience is actually determined, so this deserves more thought than the CSS ever will.

**Category** — `name`, `slug`, `description`, `sort_order`, `icon`. Seeded by you, edited rarely. Expect something like: co-ops, tutorials and classes, sports and recreation, arts and music, testing and evaluation, special needs support, umbrella and cover schools, field trips, and parent support groups.

**Program** — the core record. `name`, `slug`, `short_description` (plain text, used in listings), `description` (rich text, sanitized on save), `categories` (M2M), `website`, `email`, `phone`, `street/city/zip`, `serves_grades` or `age_min`/`age_max`, `cost_notes` (free text, because fee structures never fit a decimal field), `meeting_schedule` (free text for the same reason), `status` (draft / published / archived), `is_featured`, `logo`, `created_at`, `updated_at`, `last_verified_on`.

That last field earns its place. A directory dies of staleness, not of missing features. A `last_verified_on` date, surfaced in the admin list view and sortable, turns "is this still accurate?" from an unanswerable question into a worklist.

**ContactPerson** — inline on Program: `name`, `role`, `email`, `phone`, `is_public`. Inlines, not a separate admin section, because she should never have to navigate away from the program she is editing.

**Page** — `title`, `slug`, `body`, `is_published`. A handful of flat pages: About, FAQ, How to File a Notice of Intent in Volusia County, Resources. This is the minimum page-editing capability, and deliberately not more.

**Submission** — a public "suggest a program" form that lands in the admin as an unreviewed record she can approve into a Program with one action. **This is the feature that determines whether the directory grows without her doing all the typing**, and it should not be deferred to v2.

---

## Phases

### Phase 0 — Content inventory (her work, not yours)
Before a line of code, have her build a spreadsheet of twenty real programs with every field filled in. This surfaces the fields the model is missing far more reliably than you guessing, and it means Phase 5 starts with content already in hand. Budget a week of evenings.

### Phase 1 — Skeleton and models
Django project, the models above, migrations, a bare `ModelAdmin` for each. No styling, no public views. Done when you can add a program through `/admin` locally and see it in the database.

### Phase 2 — Deploy pipeline, early
Stand up the Hetzner server and deploy the ugly, half-finished app to a real domain over HTTPS. Doing this now rather than at the end means every subsequent change ships through a path you have already exercised a dozen times. Done when `git push` plus one command puts new code live.

### Phase 3 — Public site
List view with category filtering and text search, detail pages, the flat pages, a sitemap, and decent Open Graph tags so links look right when shared in Facebook homeschool groups — which is almost certainly how people will find this. Done when the twenty seed programs are browsable.

### Phase 4 — Admin polish
A real phase with real hours, not a cleanup pass. Specifics:

- **Install django-unfold and strip the furniture.** Unregister Groups, Sites, and anything else she will never touch. The admin should show exactly four things: Programs, Categories, Pages, Submissions.
- **Make list views scannable.** `list_display` with name, category, status, and `last_verified_on`. `search_fields` on program and contact names. `list_filter` on category and status. `list_editable` on `status` so she can publish three programs without opening three pages.
- **Write `help_text` on every ambiguous field.** That text is the documentation, and it lives exactly where she needs it.
- **Set `view_on_site`** so every record has a one-click link to its public page.
- **Add a "verified today" admin action** that bulk-sets `last_verified_on`, so an annual accuracy sweep is a morning's work rather than a chore she abandons.

Done when she adds a program you have never seen, unassisted, and does not ask you anything.

### Phase 5 — Content load and soft launch
Import the spreadsheet, have her enter another twenty by hand, then share with two or three homeschool parents you know before announcing anywhere public.

### Phase 6 — After launch
Plausible or GoatCounter for analytics (or nothing). Revisit a map view, an events calendar, and program-owner self-service only if the usage argues for them.

---

## What to buy on Hetzner

Create an account at `accounts.hetzner.com`, then work in `console.hetzner.cloud`. Create a project named something like `volusia-homeschool`, then **Add Server** with these choices:

- **Location: Ashburn, VA (us-east).** Roughly 25–35 ms from Central Florida. Do not pick a European datacenter to save two dollars; the latency is real and the visitors are all local.

- **Image: Ubuntu 24.04 LTS.** Supported to 2029 and the best-covered base for third-party install recipes. Ubuntu 26.04 LTS is available if you would rather start newer.

- **Type: Shared vCPU → the AMD tab → CPX11.** 2 shared AMD EPYC vCPUs, 2 GB RAM, 40 GB NVMe, 1 TB of US traffic. **This is the one place the obvious answer is wrong:** the CX (Intel) and CAX (Arm) lines that dominate Hetzner comparison articles are only sold in Nuremberg, Falkenstein, and Helsinki. US locations carry CPX and CCX only. Two gigabytes is comfortable for Caddy, Gunicorn with two workers, and SQLite — add a 2 GB swapfile anyway.

- **Networking: keep the Public IPv4.** It is billed separately at roughly €0.50/month. IPv6-only would save that and break access for a meaningful slice of visitors on older home routers.

- **SSH keys: add your public key, and only that.** Do not set a root password.

- **Firewall: create one and attach it.** Inbound: 80 and 443 from anywhere, 22 from your home and office IPs if they are static, from anywhere if not. Outbound: allow all. Hetzner's cloud firewall is free and sits outside the VM, which makes it strictly better than relying on UFW alone.

- **Backups: enable.** Adds 20% to the server price and retains seven automated snapshots. At roughly a dollar a month this is the cheapest insurance in the project.

- **Volumes: none.** 40 GB is enormous for this.

- **Name it** `hsd-prod-01` so a future second server is obvious.

**Expected cost:** the CPX11 in the US runs about $5.85/month, plus roughly $0.60 for IPv4 and about $1.17 for backups — **call it $7.50 to $8.00 per month, or $90 to $96 a year.** Treat that as an estimate and confirm at checkout: Hetzner raised prices twice in 2026, in April and again on 15 June, and the US locations carry a premium and a lower traffic allowance than the European ones.

**Buy the domain elsewhere.** Cloudflare Registrar sells at wholesale with no markup and no renewal games — budget $10–15/year for a `.org` or `.com`. Point an A record at the Hetzner IPv4 and let Caddy handle the certificate.

---

## Server build

In order, as root over SSH, then never as root again:

1. Create a `deploy` user with sudo, copy the authorized key over, then set `PermitRootLogin no` and `PasswordAuthentication no` in `sshd_config` and reload.
2. `apt update && apt full-upgrade`, then enable `unattended-upgrades` for security patches. This is the single line that keeps an unattended server from becoming a liability.
3. Create a 2 GB swapfile and set `vm.swappiness=10`.
4. Install `python3.12-venv`, `git`, `sqlite3`, `fail2ban`, and Caddy from the official repo.
5. App at `/srv/hsd`, virtualenv inside, secrets in an `.env` file owned by `deploy` with mode 600 and read via `django-environ`.
6. Gunicorn under a systemd unit and socket, bound to a Unix socket rather than a port.
7. A three-line Caddyfile reverse-proxying the socket. TLS is automatic; there is nothing further to configure.
8. Litestream under systemd, replicating the SQLite file to a Backblaze B2 bucket.

Write these up as a single `provision.sh` as you go, even though you will only run it once. The value is not automation — it is that the script *is* the documentation when you rebuild this in 2029.

---

## If you'd rather run containers

The container question does not change where this is hosted. It changes how code gets onto the box, and there is one constraint that quietly disqualifies most of the cheap options.

**SQLite is the filter.** The container platforms with the best pricing — Cloud Run, Azure Container Apps, App Runner — are cheap precisely because they are stateless and scale to zero. A writable database file on local disk is incompatible with that model. Cloud Run can mount a GCS bucket as a volume, but SQLite over FUSE has genuinely unsafe locking semantics and will corrupt under concurrent access. The workaround is decoupling storage to something like Turso, which adds a vendor and a network hop to solve a problem we did not have. Treat this whole category as a trap, not a bargain.

That leaves three real options:

- **Docker Compose on the same Hetzner CPX11 — $8/month, unchanged.** Caddy, Gunicorn, and a Litestream sidecar as three services in one `compose.yml`, deployed by `git pull && docker compose up -d`. You get the container workflow, reproducible builds, and a clean local-to-production parity story, at exactly the price above and with no platform whose pricing can move under you. **This is what I would do**, and nothing in the purchase instructions changes. Note that if you want a self-hosted PaaS layer on top — Coolify or Dokploy, for a Heroku-like push-to-deploy — step up to the **CPX21** (3 vCPU, 4 GB, ~$11/month), because Coolify alone will eat most of 2 GB.

- **Fly.io — roughly $3 to $5/month, genuinely cheaper.** A real managed container platform: build from your Dockerfile, `fly deploy`, done. A `shared-cpu-1x` 256 MB machine is about $2.02/month running continuously, plus $0.15/GB for the volume holding the SQLite file. The tradeoffs are that the volume pins you to one machine in one region (fine here, but it means no zero-downtime deploys without care), and that Fly has revised its pricing and free tier more than once. If minimizing dollars is the goal, this wins.

- **Railway — roughly $5/month.** The nicest developer experience of the three, volumes supported, Dockerfile or Nixpacks. The Hobby tier is $5/month including $5 of usage credit, which this app will not exceed. It is the option most exposed to vendor pricing changes, which is the same reason it is the most pleasant.

The deciding question is not cost — all three land within a few dollars a year of each other. It is what you want to be true in 2031. Hetzner plus Compose means the only thing that can change out from under you is a price adjustment on a commodity VPS. **Given that this site has no revenue and one content manager who cannot migrate it herself, that durability is worth more than the four dollars a month Fly would save.**

---

## Backups, recovery, and the bus factor

Litestream gives continuous replication with a recovery point measured in seconds, and Hetzner snapshots give a whole-machine restore. Together that covers both "I ran the wrong migration" and "the datacenter lost the disk."

Neither counts until you have **actually performed a restore once, onto a throwaway server, and timed it.** An untested backup is a belief, not a backup. Do this in Phase 4 and write down the elapsed time.

Then write a one-page `RUNBOOK.md` and put a copy somewhere she can reach without a terminal — a Google Doc is fine. It needs exactly three things: how to tell whether the site is actually down or just slow, who to contact if you are unreachable, and the fact that nothing she did caused it. **The realistic failure here is not data loss; it is her being stuck and unable to act while you are on a plane.**

---

## Ongoing cost and effort

Hetzner at roughly $8/month, a domain at $10–15/year, Backblaze B2 at well under a dollar a month at this data volume, and a transactional email provider on a free tier — Resend allows 3,000 messages a month, which is far more than password resets and contact-form mail will ever need. **Total: approximately $110 to $120 per year.**

Ongoing effort is an hour or two a quarter: review the unattended-upgrade log, apply Django patch releases, and confirm Litestream is still replicating. Budget a half-day annually for a Django minor-version upgrade.

---

## Decisions settled

**Program descriptions are rich text.** We are using `django-prose-editor`, which is ProseMirror-based and integrates cleanly with the Django admin. Two constraints make this safe rather than regrettable. First, **give the editor a narrow toolbar — bold, italic, links, bullet and numbered lists, and one heading level — and nothing else.** A full word-processor toolbar invites inconsistent typography that then has to be cleaned up by hand across hundreds of records, and the single most common way a rich text field ruins a site is font and color overrides pasted in from Word. Second, sanitize server-side with `nh3` on save against an explicit allowlist, and store the cleaned HTML; never trust the client to have done it. Rendering with `|safe` is then legitimate because the sanitization already happened at the boundary.

Keep images out of the rich text body in v1. Program logos have their own field, and allowing inline uploads means building an upload endpoint, a media pipeline, and an orphaned-file problem to solve a need that has not appeared yet.

**There is no faith-based or secular flag.** No field, no tag, and no category that reintroduces it by another name — watch for this during Phase 1, because a "Christian co-ops" category is the same decision wearing a disguise. Programs that wish to describe their affiliation can do so in their own description, in their own words, which is a meaningfully different thing from us sorting the county's families into two bins. The practical consequence is that people will occasionally ask, so it is worth a sentence on the About or FAQ page stating plainly that the directory lists programs without regard to affiliation and that parents should ask programs directly.

---

## Open decisions

- **Is a Notice of Intent walkthrough in scope?** It would likely be the most-visited page on the site and it is nearly free to write, but it commits you to keeping a piece of procedural guidance current.
- **Public submissions open, or invite-only?** Open collects more programs and more spam. A honeypot field and a moderation queue handle it, but it is a real ongoing task for her.
