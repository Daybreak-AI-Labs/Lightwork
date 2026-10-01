"""Synthetic local chat lifecycle data; no inference or customer work."""
from maverick.paths import tenant_scope


def install(app, world, tenant, principal):
    @app.post("/__browser_fixture/print", include_in_schema=False)
    async def fixture_print():
        cells = ["LongUnbrokenCell" * 12] * 8
        result = "\n".join((
            "| " + " | ".join(f"Column {index}" for index in range(8)) + " |",
            "| " + " | ".join(["---"] * 8) + " |",
            "| " + " | ".join(cells) + " |",
            "",
            "```text",
            "LongUnbrokenCode" * 120,
            "```",
            "",
            *(f"Printed paragraph {index}: all content must remain available across pages.\n"
              for index in range(1, 81)),
        ))
        with tenant_scope(tenant=tenant):
            print_goal = world.create_goal("Wide print result", "Synthetic print review", owner=principal)
            world.set_goal_status(print_goal, "done", result=result)
        return {"synthetic": True, "goal_id": print_goal}

    @app.post("/__browser_fixture/chat", include_in_schema=False)
    async def fixture_chat():
        # Dedicated records per journey. The stored transcript deliberately
        # differs from the edited result so older actions must retain its bytes.
        with tenant_scope(tenant=tenant):
            root_id = world.create_goal("Earlier request", "", owner=principal)
            world.set_goal_status(root_id, "done", result="Earlier answer after an edit.")
            conversation = world.get_or_create_conversation("web", f"{principal}/{root_id}")
            world.append_turn(conversation.id, "user", "Earlier request", goal_id=root_id)
            world.append_turn(conversation.id, "assistant", "Earlier answer as originally delivered.", goal_id=root_id)
            current_id = world.create_goal("Continue the conversation", "", owner=principal, parent_id=root_id)
            world.append_turn(conversation.id, "user", "Continue the conversation", goal_id=current_id)
            world.set_goal_status(current_id, "running")
            question_id = world.ask("Which delivery date should I use?", goal_id=current_id)
            world.answer(question_id, "Use Friday.")
        return {"synthetic": True, "root_id": root_id, "goal_id": current_id}

    @app.post("/__browser_fixture/chat/{chat_goal_id}/finish", include_in_schema=False)
    async def fixture_chat_finish(chat_goal_id: int):
        with tenant_scope(tenant=tenant):
            world.set_goal_status(chat_goal_id, "done", result="## Finished answer\n\nDelivery is planned for Friday.")
        return {"synthetic": True, "provider_calls": 0}

    @app.post("/__browser_fixture/chat/{chat_goal_id}/restart", include_in_schema=False)
    async def fixture_chat_restart(chat_goal_id: int):
        with tenant_scope(tenant=tenant):
            world.set_goal_status(chat_goal_id, "running", result="")
        return {"synthetic": True, "provider_calls": 0}
