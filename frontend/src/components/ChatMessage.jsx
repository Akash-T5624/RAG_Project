import { useState } from "react";
import { User, Bot, Copy, Check, FileSearch, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function ChatMessage({ message }) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const [showSources, setShowSources] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable
    }
  };

  return (
    <div className={`message ${isUser ? "message-user" : "message-assistant"}`}>
      <div
        className={`avatar ${
          message.isError ? "avatar-error" : isUser ? "avatar-user" : "avatar-bot"
        }`}
      >
        {isUser ? <User size={18} /> : <Bot size={18} />}
      </div>

      <div className="message-body">
        {isUser || message.isError ? (
          <div className="message-plain">
            {message.content}
          </div>
        ) : message.content ? (
          <>
            <div className="markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
            {!message.streaming && (
              <button
                onClick={handleCopy}
                className="copy-btn"
                title="Copy answer"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                {copied ? "Copied" : "Copy"}
              </button>
            )}
          </>
        ) : (
          <Loader2 size={16} className="loading-spinner" />
        )}

        {/* Streaming indicator */}
        {message.streaming && message.content && (
          <span className="stream-cursor" />
        )}

        {/* Sources */}
        {message.sources && message.sources.length > 0 && !message.streaming && (
          <div className="sources-section">
            <button
              onClick={() => setShowSources((s) => !s)}
              className="sources-toggle"
            >
              <FileSearch size={13} />
              Sources ({message.sources.length})
            </button>
            {showSources && (
              <ul className="sources-list">
                {message.sources.map((s) => (
                  <li key={s.rank} className="source-item">
                    <span className="source-page">
                      Page {s.page}
                    </span>{" "}
                    <span className="source-score">
                      (match score {typeof s.score === "number" ? s.score.toFixed(3) : s.score})
                    </span>
                    <p className="source-chunk">{s.chunk}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
