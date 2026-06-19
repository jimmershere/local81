"""Optional web-UI integration for Local-81.

Local-81 itself stays a CLI with greppable on-disk artifacts. This package
renders configuration for an *external* UI (Semaphore) that fronts the
``local81`` CLI, so adopting a UI never turns endpoints into agents or moves
local81's own state into a database.
"""
