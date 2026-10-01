"""Administrator ceilings for durable conversation admission."""
from __future__ import annotations


def entries(setting):
    group, reader = "Chat channels", "maverick.conversation_inbox"
    return (
        setting("conversation_inbox", "enabled", "Keep conversation responsive during tasks",
                "Save reviewed task guidance and editable queued messages with durable receipts. Separate tasks remain subject to current permissions, task ownership and spending limits.",
                "toggle", group, False, reader),
        setting("conversation_inbox", "guidance", "Allow reviewed guidance during a task",
                "Task owners review additional instructions before delivery at a safe checkpoint. Guidance never starts a separate task.",
                "toggle", group, True, reader),
        setting("conversation_inbox", "queue", "Allow queued conversation messages",
                "Save the next message and selected files until earlier conversation work finishes. Current access and spending limits are checked before execution.",
                "toggle", group, True, reader),
        setting("conversation_inbox", "independent", "Allow explicitly separate conversation tasks",
                "Let an owner choose a new task with no context or an explicitly selected read-only snapshot. The new task has its own execution owner and existing concurrency limits.",
                "toggle", group, True, reader),
        setting("conversation_inbox", "max_pending", "Pending messages per conversation",
                "Maximum messages awaiting review, safe delivery or task admission. Lower workspace limits can narrow this organization ceiling.",
                "number", group, 20, reader, minimum=1, maximum=100, integer=True),
    )
