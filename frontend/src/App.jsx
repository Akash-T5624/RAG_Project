import { useState, useEffect, useRef, useCallback } from "react";
import Sidebar from "./components/Sidebar.jsx";
import ChatMessage from "./components/ChatMessage.jsx";
import ChatInput from "./components/ChatInput.jsx";
import {
  uploadPdf,
  getHistory,
  sendMessageStream,
  createChat,
  listDocuments,
} from "./api.js";
import { Bot } from "lucide-react";

export default function App() {
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [activeDocId, setActiveDocId] = useState(null);
  const [chatsVersion, setChatsVersion] = useState(0);
  const messagesEndRef = useRef(null);

  const scrollToBottom = useCallback((smooth = true) => {
    messagesEndRef.current?.scrollIntoView({
      behavior: smooth ? "smooth" : "auto",
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    listDocuments()
      .then((data) => setDocuments(data.documents || []))
      .catch(() => setDocuments([]));
  }, []);

  const handleSelectChat = async (sessionId) => {
    setCurrentSessionId(sessionId);
    if (!sessionId) {
      setMessages([]);
      return;
    }
    try {
      const data = await getHistory(sessionId);
      setMessages(data.messages || []);
      setActiveDocId(data.doc_id || null);
    } catch {
      setMessages([]);
    }
  };

  const handleNewChat = async () => {
    try {
      const session = await createChat(activeDocId);
      setCurrentSessionId(session.session_id);
      setMessages([]);
      setChatsVersion((v) => v + 1);
    } catch (e) {
      console.error("Failed to create chat:", e);
    }
  };

  const handleSend = async (question) => {
    if (loading) return;

    // Lazily create a session on the first message
    let sessionId = currentSessionId;
    if (!sessionId) {
      try {
        const session = await createChat(activeDocId);
        sessionId = session.session_id;
        setCurrentSessionId(sessionId);
        setChatsVersion((v) => v + 1);
      } catch (e) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Could not start a new chat: ${e.message}`,
            isError: true,
          },
        ]);
        return;
      }
    }

    setMessages((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true },
    ]);
    setLoading(true);

    const updateLastAssistant = (updater) => {
      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === "assistant") {
            next[i] = updater(next[i]);
            break;
          }
        }
        return next;
      });
    };

    let streamError = null;
    try {
      await sendMessageStream(
        { question, session_id: sessionId, doc_id: activeDocId },
        (event) => {
          switch (event.type) {
            case "meta":
              if (event.session_id !== sessionId) setCurrentSessionId(event.session_id);
              break;
            case "sources":
              updateLastAssistant((m) => ({ ...m, sources: event.sources }));
              break;
            case "token":
              updateLastAssistant((m) => ({ ...m, content: m.content + event.content }));
              break;
            case "error":
              streamError = event.detail;
              updateLastAssistant((m) => ({
                ...m,
                content: m.content || `Error: ${event.detail}`,
                isError: !m.content,
                streaming: false,
              }));
              break;
            case "done":
              break;
            default:
              break;
          }
        }
      );
    } catch (e) {
      streamError = e.message;
    } finally {
      setLoading(false);
      updateLastAssistant((m) => ({
        ...m,
        streaming: false,
        isError: Boolean(streamError && !m.content),
        content:
          m.content ||
          (streamError ? `Error: ${streamError}` : "No response received."),
      }));
      setChatsVersion((v) => v + 1); // refresh titles in sidebar
    }
  };

  const handleUpload = async (file, onProgress) => {
    const result = await uploadPdf(file, onProgress);
    const data = await listDocuments();
    setDocuments(data.documents || []);
    setActiveDocId(result.doc_id);
    // Start a fresh chat bound to the newly uploaded document
    try {
      const session = await createChat(result.doc_id);
      setCurrentSessionId(session.session_id);
      setMessages([]);
      setChatsVersion((v) => v + 1);
    } catch {
      // chat creation is best-effort; document is still usable
    }
    return result;
  };

  const handleDocumentDeleted = (docId) => {
    setDocuments((prev) => prev.filter((d) => d.doc_id !== docId));
    if (activeDocId === docId) setActiveDocId(null);
  };

  const handleChatDeleted = (sessionId) => {
    setChatsVersion((v) => v + 1);
    if (currentSessionId === sessionId) {
      setCurrentSessionId(null);
      setMessages([]);
    }
  };

  return (
    <div className="app-root">
      <Sidebar
        currentSessionId={currentSessionId}
        onSelectChat={handleSelectChat}
        onNewChat={handleNewChat}
        onUpload={handleUpload}
        documents={documents}
        activeDocId={activeDocId}
        onSelectDocument={setActiveDocId}
        onDocumentDeleted={handleDocumentDeleted}
        chatsVersion={chatsVersion}
        onChatDeleted={handleChatDeleted}
      />

      <main className="chat-main">
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-inner">
              <Bot size={48} className="empty-state-icon" />
              <h2 className="empty-title">
                Ask questions about your PDFs
              </h2>
              <p className="empty-subtitle">
                {documents.length > 0
                  ? "Your documents are indexed. Start a conversation below."
                  : "Upload a PDF using the sidebar to get started."}
              </p>
            </div>
          </div>
        ) : (
          <div className="messages-scroll">
            {messages.map((msg, idx) => (
              <ChatMessage key={idx} message={msg} />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}

        <div className="input-bar">
          <ChatInput onSend={handleSend} loading={loading} />
        </div>
      </main>
    </div>
  );
}
