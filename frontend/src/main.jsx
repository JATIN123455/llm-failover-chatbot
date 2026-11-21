import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_URL = (
  import.meta.env.VITE_API_URL ||
  (window.location.hostname === "localhost" ? "http://localhost:8000" : "")
).replace(/\/$/, "");

function makeSessionId() {
  return `s-${crypto.randomUUID().slice(0, 8)}`;
}

function App() {
  const [sessionId] = useState(makeSessionId);
  const [messages, setMessages] = useState([]);
  const [message, setMessage] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const inputRef = useRef(null);

  async function sendMessage(event) {
    event?.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || sending) return;

    setSending(true);
    setMessage("");
    setMessages((current) => [...current, { role: "user", content: trimmed }]);

    try {
      const body = { session_id: sessionId, message: trimmed };
      if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "The request failed";
        setMessages((current) => [...current, { role: "error", content: detail }]);
      } else {
        setMessages((current) => [
          ...current,
          { role: "assistant", content: data.reply, metadata: data.metadata },
        ]);
      }
    } catch (error) {
      setMessages((current) => [
        ...current,
        { role: "error", content: "Unable to reach the backend. Please try again." },
      ]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  return (
    <main className="chat-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Resilient AI gateway</p>
          <h1>LLM Chat</h1>
        </div>
        <span className="session-badge">Session {sessionId}</span>
      </header>

      <section className="settings">
        <label htmlFor="system-prompt">System prompt</label>
        <input
          id="system-prompt"
          data-testid="system-prompt-input"
          value={systemPrompt}
          onChange={(event) => setSystemPrompt(event.target.value)}
          placeholder="Optional instructions for this chat"
        />
      </section>

      <section className="conversation" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="spark">✦</div>
            <h2>Start a conversation</h2>
            <p>Ask a question. Replies include source, model, latency, and token usage.</p>
          </div>
        )}
        {messages.map((item, index) => {
          if (item.role === "error") {
            return <div className="error-message" key={index}>{item.content}</div>;
          }
          if (item.role === "user") {
            return <div className="bubble user" key={index}>{item.content}</div>;
          }
          const meta = item.metadata;
          return (
            <div className="bubble assistant" data-testid="assistant-message" key={index}>
              <div className="reply">{item.content}</div>
              <div className="metadata">
                <span data-testid="meta-source">{meta.resolution_source}</span>
                <span data-testid="meta-model">{meta.model}</span>
                <span data-testid="meta-latency">{meta.latency_ms} ms</span>
                <span data-testid="meta-input-tokens">{meta.input_tokens}</span>
                <span data-testid="meta-output-tokens">{meta.output_tokens}</span>
              </div>
            </div>
          );
        })}
        {sending && <div className="bubble assistant thinking">Thinking...</div>}
      </section>

      <form className="composer" onSubmit={sendMessage}>
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          rows="2"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) sendMessage(event);
          }}
          placeholder="Message the assistant..."
          maxLength={4000}
        />
        <button type="submit" data-testid="send-button" disabled={!message.trim() || sending}>
          Send
        </button>
      </form>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
