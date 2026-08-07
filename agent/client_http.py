import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import ElicitResult, ServerNotification, ToolListChangedNotification

SERVER_URL = "http://127.0.0.1:8000/mcp"


async def message_handler(message):
    if isinstance(message, Exception):
        print(f"\n>>> Error from server: {message}")
        return
    if isinstance(message, ServerNotification) and isinstance(
        message.root, ToolListChangedNotification
    ):
        print("\n>>> NOTIFICATION RECEIVED: notifications/tools/list_changed <<<")
        print("The server just told us its tool list changed.\n")


async def elicitation_callback(context, params):
    print("\n>>> SUPERVISOR APPROVAL NEEDED <<<")
    print(params.message)
    schema_properties = params.requestedSchema.get("properties", {})
    answers = {}
    for field_name, field_info in schema_properties.items():
        field_type = field_info.get("type")
        if field_type == "boolean":
            raw = input(f"{field_name}? (yes/no): ").strip().lower()
            answers[field_name] = raw in ("yes", "y", "true")
        else:
            raw = input(f"{field_name}: ").strip()
            answers[field_name] = raw
    return ElicitResult(action="accept", content=answers)


async def main():
    print(f"=== Connecting over Streamable HTTP to {SERVER_URL} ===")
    async with streamablehttp_client(SERVER_URL) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(
            read_stream,
            write_stream,
            elicitation_callback=elicitation_callback,
            message_handler=message_handler,
        ) as session:
            init_result = await session.initialize()
            print("=== Connected to server (Streamable HTTP, deployed mode) ===")
            print("Server name:", init_result.serverInfo.name)
            print("Declared capabilities:", init_result.capabilities)
            print()

            tools_result = await session.list_tools()
            print("=== Available tools (front-desk agent, not yet authenticated) ===")
            for tool in tools_result.tools:
                print(f"- {tool.name}: {tool.description}")
            print()

            print("=== Testing get_flight_status ===")
            result = await session.call_tool(
                "get_flight_status", arguments={"flight_number": "BH202"}
            )
            print(result.content[0].text)
            print()

            print("=== Authenticating as supervisor sup_001 ===")
            result = await session.call_tool(
                "authenticate_supervisor",
                arguments={"supervisor_id": "sup_001", "pin": "1234"},
            )
            print(result.content[0].text)
            print()

            tools_result = await session.list_tools()
            print("=== Available tools (now authenticated as supervisor) ===")
            for tool in tools_result.tools:
                print(f"- {tool.name}: {tool.description}")
            print()

            # =========================================================
            # Memory & RAG lab — new tools
            # =========================================================

            print("=== Testing search_policy_manual ===")
            result = await session.call_tool(
                "search_policy_manual",
                arguments={
                    "query": "compensation cap for mechanical disruptions"
                }
            )
            print(result.content[0].text)
            print()

            print("=== Testing recall_flight_history ===")
            result = await session.call_tool(
                "recall_flight_history",
                arguments={
                    "flight_number": "BH202",
                    "current_question": "any duty-hour overrides on record?"
                }
            )
            print(result.content[0].text)
            print()

            print("=== Testing run_memory_consolidation (supervisor-only) ===")
            result = await session.call_tool(
                "run_memory_consolidation",
                arguments={}
            )
            print(result.content[0].text)
            print()


if __name__ == "__main__":
    asyncio.run(main())
