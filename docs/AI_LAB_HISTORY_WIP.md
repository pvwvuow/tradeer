# 19b work in progress (AI Lab history rail)

Pushed on 9 October 2026 before a PR: app/ai/chat_store.py, the new app/ui/ai_chat.py
(history rail, folded thinking line, budget line, Esc / Ctrl+Enter) and
tests/unit/test_ai_chat_store.py.

Still to do before the PR:

- app/ui/ai_lab_page.py: `from app.ai.chat_store import CHAT_FOLDER, ChatStore`, then
  `chats = ChatStore(context.export_dir / CHAT_FOLDER) if context is not None else None`
  and `ChatPanel(self.llm_panel.make_client, self.agent_tools, store=chats)`; docstring
  line about saved chats.
- tests/ui/test_ai_chat.py: the head is `view.head` (ends with "1 tool · 2.0k tokens"),
  steps fold (`steps_open`, `toggle_steps`, SHOW_STEPS / HIDE_STEPS), budget text, and a
  rail test: save, list, reopen, search, pin, Pinned filter, delete (set `chat.confirm`).
- docs/UI_V2.md status line, then PR, CI, merge, release 0.32.0, delete this file.
