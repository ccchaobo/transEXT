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

export async function consumeAssistantEvents(response,onEvent){
  if(!response.ok){const payload=await response.json();const detail=payload.detail || payload;throw new ApiError(detail.message || `请求失败（HTTP ${response.status}）`,{code:detail.error_code,status:response.status});}
  if(!response.body)throw new ApiError('浏览器未提供流式响应。');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result=null,finished=false;
  const consume=frame=>{
    let kind='message';const data=[];
    for(const line of frame.split('\n')){if(line.startsWith('event:'))kind=line.slice(6).trim();else if(line.startsWith('data:'))data.push(line.slice(5).trimStart());}
    if(!data.length)return;
    let value;try{value=JSON.parse(data.join('\n'));}catch{throw new ApiError('流式响应格式损坏，已收到的内容会保留。');}
    if(kind==='error')throw new ApiError(value.message,{code:value.code});
    onEvent?.(kind,value);if(kind==='done')result=value;
  };
  try{
    while(true){const {done,value}=await reader.read();buffer+=decoder.decode(value || new Uint8Array(),{stream:!done});buffer=buffer.replace(/\r\n/g,'\n');
      let split;while((split=buffer.indexOf('\n\n'))>=0){consume(buffer.slice(0,split));buffer=buffer.slice(split+2);}if(done){finished=true;break;}}
    if(buffer.trim())consume(buffer);
    if(!result)throw new ApiError('连接结束但回答未完成，已保留收到的内容；未自动重试。',{code:'assistant_unknown_result'});
    return result;
  }finally{if(!finished){try{await reader.cancel();}catch{}}reader.releaseLock();}
}

async function streamAssistant(id,body,{signal,onEvent}={}){
  const response=await fetch(`${BASE}/documents/${id}/assistant/stream`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal});
  return consumeAssistantEvents(response,onEvent);
}

export const api = {
  zoteroStatus: () => request(`${BASE}/zotero/status`),
  zoteroPull: library_id => request(`${BASE}/zotero/pull`,{method:"POST",body:{library_id}}),
  zoteroItems: params => request(`${BASE}/zotero/items?${new URLSearchParams(params)}`),
  zoteroImportPdf: (library_id,key) => request(`${BASE}/zotero/import-pdf`,{method:"POST",body:{library_id,key}}),
  zoteroPush: body => request(`${BASE}/zotero/push`,{method:"POST",body}),
  zoteroAssociate: body => request(`${BASE}/zotero/associate`,{method:"POST",body}),
  zoteroResolve: body => request(`${BASE}/zotero/resolve-from-zotero`,{method:"POST",body}),
  zoteroBinding: id => request(`${BASE}/zotero/binding/${id}`),
  zoteroCapturePreview: source => request(`${BASE}/zotero/capture-preview`,{method:"POST",body:{source}}),
  zoteroCaptureSave: body => request(`${BASE}/zotero/capture-save`,{method:"POST",body}),
  zoteroDuplicates: library_id => request(`${BASE}/zotero/duplicates?library_id=${library_id}`),
  zoteroMerge: body => request(`${BASE}/zotero/merge`,{method:"POST",body}),
  zoteroNative: (command,body) => request(`${BASE}/zotero/native/${command}`,{method:"POST",body}),
  zoteroExportUrl: (keys,format="bibtex",style="apa",library_id=0) => `${BASE}/zotero/export?${new URLSearchParams({keys:keys.join(","),format,style,library_id})}`,
  libraryOverview: () => request(`${BASE}/library/overview`),
  libraryItems: (params = {}) => request(`${BASE}/library/items?${new URLSearchParams(params)}`),
  libraryItem: id => request(`${BASE}/library/items/${id}`),
  editLibraryItem: (id, body) => request(`${BASE}/library/items/${id}`, {method:"PATCH",body}),
  libraryBatch: body => request(`${BASE}/library/batch`, {method:"POST",body}),
  createCollection: name => request(`${BASE}/library/collections`, {method:"POST",body:{name}}),
  renameCollection: (id,name) => request(`${BASE}/library/collections/${id}`, {method:"PATCH",body:{name}}),
  deleteCollection: id => request(`${BASE}/library/collections/${id}`, {method:"DELETE"}),
  askAssistant: (id, payload) => request(`${BASE}/documents/${id}/assistant`, { method: "POST", body: payload }),
  streamAssistant,
  assistantCapabilities: () => request(`${BASE}/assistant/capabilities`),
  assistantContext: id => request(`${BASE}/documents/${id}/assistant/context`),
  health: () => request("/health"),
  translationConfiguration: () => request(`${BASE}/translation/configuration`),

  /* Translation settings.  The key travels one way: the reader types it, the browser sends it
     to this machine's own backend, and no endpoint ever sends it back.  `getTranslationSettings`
     therefore always reports the key as a boolean. */
  getTranslationSettings: () => request(`${BASE}/settings/translation`),
  putTranslationSettings: (payload) =>
    request(`${BASE}/settings/translation`, { method: "PUT", body: payload }),
  clearTranslationSettings: () =>
    request(`${BASE}/settings/translation`, { method: "DELETE" }),
  testTranslationSettings: () =>
    request(`${BASE}/settings/translation/test`, { method: "POST" }),

  listDocuments: (offset = 0) => request(`${BASE}/documents?limit=100&offset=${offset}`),
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
