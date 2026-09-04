# Public API

The supported import surface is re-exported from `threvo_actions`. Optional
adapter types live in their own modules so importing the core never imports a
database or agent framework.

!!! info "Versioned support"

    Version `0.1.5` is the current supported exact root Python API and CLI.
    Migrating from `0.1.4` requires the release record's host changes. The
    documented `threvo_actions.experimental` namespace follows its
    separate 120-day evaluation window; it is intentionally absent from the
    root export. Serialized interoperability forms also remain experimental.
    See [Versioning](../versioning.md).

::: threvo_actions
    options:
      members: true
      show_source: false
