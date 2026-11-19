# JSON interface

Commands that support `--json` return one object:

```json
{
  "ok": true,
  "command": "task.list",
  "data": {},
  "warnings": [],
  "problems": []
}
```

`command` names the operation. `data` contains its result. Problems report errors; warnings provide additional context. Each diagnostic has a stable code and message, with a source path when relevant.

Task output represents the current Markdown record and its relationships. A parent link identifies another Task. Handoff text is authored guidance; missing optional hints do not prevent reading or changing status.

A successful status change confirms the requested document operation. It does not claim that a product test, review, commit, or deployment happened.

Source-navigation results include source identity and analysis coverage. A partial or unavailable result does not establish that a symbol or dependency is absent. Paths in repository results are repository-relative; an explicitly named workspace path is workspace-relative.

The 1.0 interface removes the previous binding, receipt, ownership, catalogue, metadata, and field-gate protocol. Consumers of older command-specific fields must update rather than relying on synthetic compatibility values.
