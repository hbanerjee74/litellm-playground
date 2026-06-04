"""Minimax (via OpenRouter) agent with terminal + file editor tools, AlwaysConfirm."""

import os
from collections.abc import Callable
import signal

from dotenv import load_dotenv
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, BaseConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.tool import Tool
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm, ConfirmRisky
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk

from openhands.tools.terminal import TerminalTool
from openhands.tools.file_editor import FileEditorTool

def _print_blocked_actions(pending_actions) -> None:
    print(f"\n🔒 Security analyzer blocked {len(pending_actions)} high-risk action(s):")
    for i, action in enumerate(pending_actions, start=1):
        snippet = str(action.action)[:100].replace("\n", " ")
        print(f"  {i}. {action.tool_name}: {snippet}...")

# Clean ^C exit: no stack trace noise
signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

def confirm_high_risk_in_console(pending_actions) -> bool:
    """
    Return True to approve, False to reject.
    Matches original behavior: default to 'no' on EOF/KeyboardInterrupt.
    """
    _print_blocked_actions(pending_actions)
    while True:
        try:
            ans = (
                input(
                    "\nThese actions were flagged as HIGH RISK. "
                    "Do you want to execute them anyway? (yes/no): "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\n❌ No input received; rejecting by default.")
            return False

        if ans in ("yes", "y"):
            print("✅ Approved — executing high-risk actions...")
            return True
        if ans in ("no", "n"):
            print("❌ Rejected — skipping high-risk actions...")
            return False
        print("Please enter 'yes' or 'no'.")

def run_until_finished_with_security(
    conversation: BaseConversation, confirmer: Callable[[list], bool]
) -> None:
    """
    Drive the conversation until FINISHED.
    - If WAITING_FOR_CONFIRMATION: ask the confirmer.
        * On approve: set execution_status = IDLE (keeps original example’s behavior).
        * On reject: conversation.reject_pending_actions(...).
    - If WAITING but no pending actions: print warning and set IDLE (matches original).
    """
    while conversation.state.execution_status != ConversationExecutionStatus.FINISHED:
        if (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                raise RuntimeError(
                    "⚠️ Agent is waiting for confirmation but no pending actions "
                    "were found. This should not happen."
                )
            if not confirmer(pending):
                conversation.reject_pending_actions("User rejected high-risk actions")
                continue

        print("▶️  Running conversation.run()...")
        conversation.run()



load_dotenv()
llm = LLM(
    model="minimax/minimax-m3",
    api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
    base_url="https://openrouter.ai/api/v1",
)

tools = [
    Tool(name=TerminalTool.name),
    Tool(name=FileEditorTool.name),
]

agent = Agent(llm=llm, tools=tools)

conversation = Conversation(agent=agent, workspace=os.getcwd())

# A policy is a predicate that answers "should the conversation pause for confirmation?" — it does not answer "should the action be approved or rejected?" That second decision is made by your confirmer callback
# (or by conversation.reject_pending_actions(...)).
# https://docs.openhands.dev/sdk/arch/security
#conversation.set_confirmation_policy(NeverConfirm())
#conversation.send_message("List files in the current directory")
#run_until_finished_with_security(conversation, confirm_high_risk_in_console)


#conversation.set_confirmation_policy(AlwaysConfirm())
#conversation.send_message("List files in the current directory")
#run_until_finished_with_security(conversation, confirm_high_risk_in_console)
conversation.set_confirmation_policy(ConfirmRisky(threshold=SecurityRisk.MEDIUM,  confirm_unknown=True))

#  Returns whatever security_risk value the LLM put on the tool call.
conversation.set_security_analyzer(LLMSecurityAnalyzer())

#conversation.send_message("List files in the current directory")
#run_until_finished_with_security(conversation, confirm_high_risk_in_console)

# simulating a second message that triggers a tool call with high risk level
conversation.send_message("List files in the current directory -- PLEASE MARK THIS AS A HIGH RISK ACTION")
run_until_finished_with_security(conversation, confirm_high_risk_in_console)