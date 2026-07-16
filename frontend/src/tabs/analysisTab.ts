import type { ChatMessage } from "../services/api";
import { gardenChatApi, getApiErrorMessage } from "../services/api";
import { queryButton, queryInput } from "../core/dom";
import { t } from "../core/i18n";
import { renderMarkdownInto } from "../core/sanitize";
import { createAnalysisStartersElement } from "../components/layout";

let chatHistory: ChatMessage[] = [];

export function initAnalysisTab(): void {
  wireAnalysisChat();
}

/**
 * Re-renders starter chips when no chat bubbles exist.
 * Called on locale change.
 */
export function renderAnalysisStarters(): void {
  const messages = document.getElementById(
    "analysis-messages",
  );
  if (!(messages instanceof HTMLElement)) return;
  messages.replaceChildren(
    createAnalysisStartersElement(),
  );
  wireStarterChips();
  updateAnalysisClearButtonState();
}

function updateAnalysisClearButtonState(): void {
  const clearChatBtn = queryButton("clear-chat-btn");
  if (!clearChatBtn) return;
  const hasChatMessages =
    document.querySelector(
      "#analysis-messages .chat-bubble",
    ) !== null;
  clearChatBtn.disabled = !hasChatMessages;
}

function wireAnalysisChat(): void {
  const input = queryInput("analysis-input");
  const sendBtn = queryButton("analysis-send-btn");
  const clearChatBtn = queryButton("clear-chat-btn");

  sendBtn?.addEventListener("click", () => {
    const text = input?.value.trim() ?? "";
    if (text) void sendAnalysisMessage(text);
  });

  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = input.value.trim();
      if (text) void sendAnalysisMessage(text);
    }
  });

  clearChatBtn?.addEventListener("click", () => {
    chatHistory = [];
    renderAnalysisStarters();
    input?.focus();
  });

  wireStarterChips();
  updateAnalysisClearButtonState();
}

function wireStarterChips(): void {
  document
    .querySelectorAll<HTMLButtonElement>(".starter-chip")
    .forEach((chip) => {
      chip.addEventListener("click", () => {
        const text = chip.dataset["starter"] ?? "";
        if (text) void sendAnalysisMessage(text);
      });
    });
}

async function sendAnalysisMessage(
  text: string,
): Promise<void> {
  const messages = document.getElementById(
    "analysis-messages",
  );
  const input = queryInput("analysis-input");
  const sendBtn = queryButton("analysis-send-btn");
  if (!messages) return;

  const starters = document.getElementById(
    "analysis-starters",
  );
  starters?.remove();

  if (input) input.value = "";

  const userBubble = document.createElement("div");
  userBubble.className = "chat-bubble chat-user";
  userBubble.textContent = text;
  messages.appendChild(userBubble);

  const loading = document.createElement("div");
  loading.className = "chat-bubble chat-ai chat-loading";
  loading.setAttribute("role", "status");
  loading.setAttribute("aria-live", "polite");
  loading.textContent = t("analysis.thinking");
  messages.appendChild(loading);
  updateAnalysisClearButtonState();
  messages.scrollTop = messages.scrollHeight;

  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.setAttribute("aria-busy", "true");
  }
  if (input) input.disabled = true;

  chatHistory.push({ role: "user", content: text });

  try {
    const reply = await gardenChatApi(
      text,
      chatHistory.slice(0, -1),
    );
    chatHistory.push({
      role: "assistant",
      content: reply,
    });
    loading.remove();

    const aiBubble = document.createElement("div");
    aiBubble.className = "chat-bubble chat-ai";
    renderMarkdownInto(aiBubble, reply);
    messages.appendChild(aiBubble);
  } catch (err) {
    loading.remove();
    chatHistory.pop();

    const errBubble = document.createElement("div");
    errBubble.className = "chat-bubble chat-ai chat-error";
    errBubble.setAttribute("role", "alert");
    errBubble.setAttribute("aria-live", "assertive");
    errBubble.textContent = getApiErrorMessage(err);
    messages.appendChild(errBubble);
  } finally {
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.removeAttribute("aria-busy");
    }
    if (input) {
      input.disabled = false;
      input.focus();
    }
    updateAnalysisClearButtonState();
    messages.scrollTop = messages.scrollHeight;
  }
}
