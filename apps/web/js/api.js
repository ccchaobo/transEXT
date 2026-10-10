/* Thin wrapper over the HTTP API.  Every function returns parsed JSON and throws an ApiError
   carrying the server's own error code, so the UI can explain a refusal in the server's words
   instead of inventing a message of its own. */

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, { code = null, status = 0, detail = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, form, raw = false } = {}) {
  const init = { method, headers: {} };
  if (form) {
    init.body = form;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    // A dropped connection is not an HTTP error and must not be reported as one.
    throw new ApiError("无法连接到本地服务，请确认后端仍在运行。", { code: "network" });
  }
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = raw ? text : { message: text }; }
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : payload;
    const code = (detail && detail.error_code) || `http_${response.status}`;
    let message = (detail && detail.message) || `请求失败（HTTP ${response.status}）`;
    if (Array.isArray(detail)) {
      message = detail.map((item) => `${(item.loc || []).join(".")}: ${item.msg}`).join("; ");
    }
    throw new ApiError(message, { code, status: response.status, detail });
  }
  return payload;
}

export const api = {
  health: () => request("/health"),
  translationConfiguration: () => request(`${BASE}/translation/configuration`),

  listDocuments: () => request(`${BASE}/documents`),
  getDocument: (id) => request(`${BASE}/documents/${id}`),
  deleteDocument: (id) => request(`${BASE}/documents/${id}?confirm=true`, { method: "DELETE" }),
  outline: (id) => request(`${BASE}/documents/${id}/outline`),
  page: (id, index, targetLanguage) => {
    const query = targetLanguage ? `?target_language=${encodeURIComponent(targetLanguage)}` : "";
    return request(`${BASE}/documents/${id}/pages/${index}${query}`);
  },
  originalUrl: (id) => `${BASE}/documents/${id}/original`,
  pageImageUrl: (id, index, kind = "preview") =>
    `${BASE}/documents/${id}/pages/${index}/${kind}.png`,

  upload: (file) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request(`${BASE}/documents`, { method: "POST", form });
  },

  startTranslation: (id, payload) =>
    request(`${BASE}/documents/${id}/translate`, { method: "POST", body: payload }),
  listTranslations: (id, targetLanguage) => {
    const query = targetLanguage ? `?target_language=${encodeURIComponent(targetLanguage)}` : "";
    return request(`${BASE}/documents/${id}/translations${query}`);
  },
  correctTranslation: (translationId, text) =>
    request(`${BASE}/translations/${translationId}`, { method: "PATCH", body: { text } }),

  listJobs: (documentId) =>
    request(`${BASE}/jobs?document_id=${encodeURIComponent(documentId)}`),
  getJob: (jobId) => request(`${BASE}/jobs/${jobId}`),
  jobEvents: (jobId, since) => request(`${BASE}/jobs/${jobId}/events?since=${since}`),
  cancelJob: (jobId) => request(`${BASE}/jobs/${jobId}/cancel`, { method: "POST" }),
  retryJob: (jobId) => request(`${BASE}/jobs/${jobId}/retry`, { method: "POST", body: {} }),

  listAnnotations: (id) => request(`${BASE}/documents/${id}/annotations`),
  createAnnotation: (id, payload) =>
    request(`${BASE}/documents/${id}/annotations`, { method: "POST", body: payload }),
  deleteAnnotation: (annotationId) =>
    request(`${BASE}/annotations/${annotationId}`, { method: "DELETE" }),

  getReadingState: (id) => request(`${BASE}/documents/${id}/reading-state`),
  putReadingState: (id, payload) =>
    request(`${BASE}/documents/${id}/reading-state`, { method: "PUT", body: payload }),

  exportUrl: (id, targetLanguage) =>
    `${BASE}/documents/${id}/export.md?target_language=${encodeURIComponent(targetLanguage)}`,
};
