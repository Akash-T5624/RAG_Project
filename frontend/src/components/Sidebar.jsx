import {
  Plus,
  MessageCircle,
  Trash2,
  Upload,
  FileText,
  Pencil,
  Check,
  X,
  Loader2,
} from "lucide-react";
import { useEffect, useState, useRef, useCallback } from "react";
import {
  listChats,
  deleteChat,
  deleteDocument,
  renameChat,
} from "../api";

export default function Sidebar({
  currentSessionId,
  onSelectChat,
  onNewChat,
  onUpload,
  documents,
  activeDocId,
  onSelectDocument,
  onDocumentDeleted,
  chatsVersion,
  onChatDeleted,
}) {
  const [chats, setChats] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const fileInputRef = useRef(null);

  const fetchChats = useCallback(async () => {
    try {
      const data = await listChats();
      setChats(data.chats || []);
    } catch {
      // keep previous list on failure
    }
  }, []);

  useEffect(() => {
    fetchChats();
  }, [chatsVersion, fetchChats]);

  const handleNewChat = async () => {
    if (!documents.length && !activeDocId) {
      setUploadError("Upload a PDF first so the assistant has something to read.");
      return;
    }
    await onNewChat();
  };

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError(null);
    setUploading(true);
    setUploadProgress(0);
    try {
      await onUpload(file, setUploadProgress);
    } catch (err) {
      const detail =
        err?.response?.data?.detail || err.message || "Upload failed.";
      setUploadError(String(detail));
    } finally {
      setUploading(false);
      setUploadProgress(0);
      if (fileInputRef.current) fileInputRef.current.value = null;
    }
  };

  const handleDeleteChat = async (id, e) => {
    e.stopPropagation();
    try {
      await deleteChat(id);
      onChatDeleted(id);
    } catch {
      // ignore
    }
  };

  const handleDeleteDoc = async (docId, e) => {
    e.stopPropagation();
    try {
      await deleteDocument(docId);
      onDocumentDeleted(docId);
    } catch (err) {
      setUploadError(err?.response?.data?.detail || "Could not delete document.");
    }
  };

  const startRename = (chat, e) => {
    e.stopPropagation();
    setRenamingId(chat.session_id);
    setRenameValue(chat.title === "New chat" ? "" : chat.title);
  };

  const commitRename = async () => {
    const id = renamingId;
    const title = renameValue.trim();
    setRenamingId(null);
    if (!id || !title) return;
    try {
      await renameChat(id, title);
      fetchChats();
    } catch {
      // ignore
    }
  };

  return (
    <div className="sidebar">
      <div className="sidebar-newchat">
        <button
          onClick={handleNewChat}
          disabled={uploading}
          className="new-chat-btn"
        >
          <Plus size={16} />
          New chat
        </button>
      </div>

      {/* Documents section */}
      <div className="docs-section">
        <div className="section-header">
          <span className="section-label">
            Documents
          </span>
          <label className="upload-label">
            {uploading ? (
              <Loader2 size={12} className="spin" />
            ) : (
              <Upload size={12} />
            )}
            {uploading ? `${uploadProgress}%` : "Upload PDF"}
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,application/pdf"
              className="hidden-input"
              onChange={handleUpload}
              disabled={uploading}
            />
          </label>
        </div>

        {uploading && (
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: `${Math.max(uploadProgress, 5)}%` }}
            />
          </div>
        )}

        {uploadError && (
          <div className="upload-error">
            <span className="upload-error-text">{uploadError}</span>
            <button onClick={() => setUploadError(null)} aria-label="Dismiss">
              <X size={12} />
            </button>
          </div>
        )}

        {documents.length === 0 ? (
          <p className="no-docs">
            No documents yet.
          </p>
        ) : (
          <ul className="doc-list">
            {documents.map((doc) => (
              <li key={doc.doc_id}>
                <button
                  onClick={() => onSelectDocument(doc.doc_id)}
                  title={`${doc.filename}${doc.pages ? ` • ${doc.pages} pages` : ""}`}
                  className={`doc-item ${doc.doc_id === activeDocId ? "active" : ""}`}
                >
                  <FileText size={13} className="doc-icon" />
                  <span className="doc-name">{doc.filename}</span>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => handleDeleteDoc(doc.doc_id, e)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleDeleteDoc(doc.doc_id, e);
                    }}
                    className="doc-delete"
                    title="Delete document"
                  >
                    <Trash2 size={11} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Chats */}
      <div className="history-header">
        <span className="section-label">History</span>
      </div>
      <div className="chats-scroll">
        {chats.length === 0 ? (
          <div className="no-chats">
            No chats yet. Ask your first question.
          </div>
        ) : (
          <ul className="chat-list">
            {chats.map((chat) => (
              <li
                key={chat.session_id}
                onClick={() =>
                  renamingId !== chat.session_id && onSelectChat(chat.session_id)
                }
                className={`chat-item ${chat.session_id === currentSessionId ? "active" : ""}`}
              >
                {renamingId === chat.session_id ? (
                  <>
                    <Pencil size={14} className="rename-icon" />
                    <input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename();
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      onClick={(e) => e.stopPropagation()}
                      className="rename-input"
                    />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        commitRename();
                      }}
                      className="rename-save"
                      title="Save"
                    >
                      <Check size={13} />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setRenamingId(null);
                      }}
                      className="rename-cancel"
                      title="Cancel"
                    >
                      <X size={13} />
                    </button>
                  </>
                ) : (
                  <>
                    <MessageCircle size={15} style={{ flexShrink: 0, opacity: 0.7 }} />
                    <span className="chat-title" title={chat.title}>
                      {chat.title || "New chat"}
                    </span>
                    <button
                      onClick={(e) => startRename(chat, e)}
                      className="chat-action"
                      title="Rename"
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      onClick={(e) => handleDeleteChat(chat.session_id, e)}
                      className="chat-action chat-action-danger"
                      title="Delete"
                    >
                      <Trash2 size={12} />
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="sidebar-footer">
        RAG Chat &middot; powered by Groq
      </div>
    </div>
  );
}
