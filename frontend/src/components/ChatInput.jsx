import { useState, useRef, useEffect } from "react";
import { Send, Loader2 } from "lucide-react";

const TEXTAREA_MAX_HEIGHT = 160;

export default function ChatInput({ onSend, loading }) {
  const [text, setText] = useState("");
  const textareaRef = useRef(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const contentHeight = el.scrollHeight + 2; // compensate element border
    if (contentHeight > TEXTAREA_MAX_HEIGHT) {
      el.style.height = `${TEXTAREA_MAX_HEIGHT}px`;
      el.style.overflowY = "auto";
    } else {
      el.style.height = `${contentHeight}px`;
      el.style.overflowY = "hidden";
    }
  }, [text]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (text.trim() && !loading) {
      onSend(text.trim());
      setText("");
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="chat-form">
      <textarea
        ref={textareaRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask a question about your document..."
        rows={1}
        className="chat-textarea"
      />
      <button
        type="submit"
        disabled={!text.trim() || loading}
        className="send-btn"
        title={loading ? "Waiting for response..." : "Send"}
      >
        {loading ? <Loader2 size={20} className="spin" /> : <Send size={20} />}
      </button>
    </form>
  );
}
