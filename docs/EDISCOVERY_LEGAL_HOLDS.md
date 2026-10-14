# Legal holds and matter export (eDiscovery)

A **legal matter** holds the records of named people for a legal dispute. It is
off by default. Turn it on with:

```toml
[ediscovery]
enable = true
```

The setup wizard asks the same question ("Legal holds and matter export?"). When the
switch is off, there are no matter routes, no Legal holds section, and no matter
records. A matter recorded while the switch was on keeps working as a hold until
it is released, even if the switch is later turned off.

## Who can manage matters

Creating, viewing, editing, releasing and exporting a matter need the **audit**
permission. The administrator role holds it too. Viewers and operators get a
refusal. The Legal holds section appears on the **Compliance** page only for people
with audit access.

## What a matter holds

| Field | Meaning |
| --- | --- |
| Name, description | Shown to people with audit access only. |
| Custodians | Principal identifiers, one per line. A dashboard user is their user name. A messaging account is `user:<channel>:<identifier>`. |
| From / To (optional) | Calendar dates in UTC. Both ends are inclusive. |
| Records to keep | Any of: tasks and their runs, chat conversations, attachments, approval requests. |
| Status | `active` or `released`. Releasing is final for that matter and is audited. |

Every create, edit and release writes an audit event that records the matter ID,
who acted, the revision and which fields changed. Audit events never name the
custodians. A change is saved only when its audit event is written.

## What an active matter blocks

**Bulk deletion of old records is paused for the whole workspace.** This covers
retention enforcement, `maverick gc`, the dashboard "Tidy up old records" action and
whole-client erasure. Any active matter blocks these, because the stores cannot
reliably tell which records belong to which custodian.

**Erasing a person is refused only for records in scope.** A closure is refused
when it contains a record that is in the matter's:

- custodians (the record's owner is a custodian, or the conversation belongs to one),
- record types, and
- date range (see below).

Records outside the scope stay erasable: other people's records, and records
outside the date range. The refusal says only that a legal hold applies to some of
these records. It never names the matter.

This check runs in the CLI `maverick erase` command, in the admin "erase a person"
action, and in self-service deletion. Self-service deletion reports the closure as
**held** and erases nothing from it.

### Date rules

Each record is tested against its own timestamp:

- task: creation time; run: start time;
- chat: any turn in the range (a conversation with no turn in the range is out of scope);
- attachment: upload time;
- approval request: request time.

A closure that mixes in-scope and out-of-scope records is refused as a whole. A
partial erasure is never performed.

If a matter's records cannot be listed completely (more than 100,000 records of one
kind), the erasure is refused. The check fails closed.

## Export

An auditor exports a matter as a ZIP archive:

- `goals.jsonl`, `chats.jsonl`, `attachments.jsonl`, `approvals.jsonl`: one file per
  selected record type, holding only the in-scope records, one JSON object per line.
- `manifest.json`: the matter definition, the SHA-256 and size of every file, the
  record counts, what is not included, and a signature.

The signature uses the workspace's audit signing identity (Ed25519 over a
domain-separated SHA-256 of the canonical manifest). It is the same signature used
for other exports. The signature proves the archive was produced by this
installation and has not changed since. It does not prove who requested it.

The export is streamed to a private temporary file and removed after download.
Archives larger than 256 MiB are refused with a message to narrow the matter. The
export is audited. If the audit record cannot be written, no archive is released.

To check an archive offline, use `maverick.ediscovery_export.verify_export` with
the audit public keys. It recomputes the manifest digest, checks the signature
against the audit key registry, and checks every file's size, hash and record count.
Any entry that is missing, extra or changed is reported by name.

## What is not covered

- **Attachment file bytes** are not copied into the archive. Each attachment row has
  its SHA-256 and size. The files stay in their own store.
- **Audit evidence, knowledge-base copies, model memory, backups and copies held by
  outside providers** are outside the matter. A matter does not change their retention.
- **Approvals** are never deleted by any erasure path, so no erasure is refused for
  them. They are held in the export only.
- **Incident attachments** (the incident-attachment register) have their own delete
  action. That action does not check legal matters.
- **Direct database calls** to the world-model erase primitive do not check matters
  themselves. Every product path listed above does.
- **Mixed closures** are refused whole. There is no partial erasure to keep out-of-scope
  records while in-scope ones stay.

## Releasing a hold

Open the matter in the Legal holds section, choose **Release this hold**, and confirm.
Released matters no longer block anything. Their records and audit history remain.
