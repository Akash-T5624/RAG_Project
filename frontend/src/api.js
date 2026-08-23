import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "";

const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
});

export const uploadPdf = async (file, onProgress) => {
  const form = new FormData();
  form.append("file", file);
  const res = await api.post("/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
    },
    timeout: 600000,
  });
  return res.data;
};

export const listDocuments = async () => {
  const res = await api.get("/documents");
  return res.data;
};

export const deleteDocument = async (docId) => {
  const res = await api.delete(`/documents/${docId}`);
  return res.data;
};

export const createChat = async (docId) => {
  const res = await api.post(
    "/chat/new",
    null,
    docId ? { params: { doc_id: docId } } : {}
  );
  return res.data;
};

export const listChats = async () => {
  const res = await api.get("/chats");
  return res.data;
};

export const getHistory = async (sessionId) => {
  const res = await api.get(`/history/${sessionId}`);
  return res.data;
};

export const deleteChat = async (sessionId) => {
  const res = await api.delete(`/history/${sessionId}`);
  return res.data;
};

export const renameChat = async (sessionId, title) => {
  const res = await api.patch(`/history/${sessionId}`, { title });
  return res.data;
};

/**
 * Stream a chat answer over SSE.
 * onEvent receives parsed events:
 *   {type:"meta", session_id, doc_id}
 *   {type:"sources", sources:[...]}
 *   {type:"token", content}
 *   {type:"done"}
 *   {type:"error", detail}
 */
export const sendMessageStream = async (payload, onEvent, signal) => {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const err = await res.json();
      if (err.detail) detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = rawEvent.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch {
        // ignore malformed events
      }
    }
  }
};
