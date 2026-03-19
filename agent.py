"""
agent.py — LangGraph-based agent loop (per-user, stateless version)

Accepts per-user tool_functions / tool_specs instead of globals.
"""

import json
from typing import TypedDict, Literal
from openai import OpenAI

client = OpenAI()  # reads OPENAI_API_KEY from env

SYSTEM_PROMPT = """당신은 뤼튼의 내부 업무 자동화 에이전트입니다.

역할:
- Notion에서 작업을 조회·업데이트합니다
- Slack으로 팀에게 알림을 보냅니다
- 반복적이고 수동적인 업무를 자동화합니다

원칙:
- 작업 전에 무엇을 할지 한 줄로 설명합니다
- 도구 실행 결과를 바탕으로 다음 단계를 결정합니다
- 모든 작업이 끝나면 결과를 간결하게 요약합니다
- 한국어로 응답합니다
- Slack 채널이 명시되지 않은 경우 항상 "all-todo-list" 채널을 사용합니다

Notion 규칙 (반드시 준수):
- 상태 변경이 필요한 경우: 반드시 get_notion_tasks로 기존 작업을 먼저 조회한 뒤 update_notion_task_status를 호출합니다
- 존재하지 않는 작업은 수정하지 않습니다
"""


class AgentState(TypedDict):
    messages:     list[dict]
    tool_calls:   list
    final_answer: str


def run_agent(
    user_message: str,
    tool_functions: dict,
    tool_specs: list,
    history: list[dict] | None = None,
) -> str:
    """
    Run the agent for a single user message.

    Args:
        user_message:   The user's request
        tool_functions: Per-user tool function dict from build_tools()
        tool_specs:     Per-user tool specs list from build_tools()
        history:        Prior conversation turns [{"role": ..., "content": ...}]

    Returns:
        Final assistant response string
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # Agentic loop (max 10 iterations to prevent runaway)
    for _ in range(10):
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tool_specs,
            tool_choice="auto",
        )

        msg = response.choices[0].message

        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        if not msg.tool_calls:
            return msg.content or ""

        # Execute tools
        for tc in msg.tool_calls:
            func_name = tc.function.name
            args      = json.loads(tc.function.arguments)
            func      = tool_functions.get(func_name)
            result    = func(**args) if func else f"알 수 없는 도구: {func_name}"
            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result,
            })

    return "최대 반복 횟수에 도달했습니다."
