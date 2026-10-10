/* The translation settings panel.
 *
 * This is the reader's own way in.  Before it existed the only way to tell the service where to
 * send anything was to edit a .env file and restart *both* processes, and the API's own 409
 * message said as much.  Two things were wrong with that, and both are fixed here:
 *
 *   - a value written to .env is read once at startup, so the worker already running never saw
 *     it - the reader configured a key and watched translation keep failing;
 *   - the API key had to be typed into a file by hand, which for a self-hosted tool used by one
 *     person is friction with no benefit.
 *
 * Note what this file does *not* do.  It sends the key to this machine's own backend once and
 * never asks for it back: the response reports a boolean, and there is no code path here that
 * can render a stored key into the page.  A blank key field means "leave the stored key alone",
 * which is why blank is not the same as clearing - clearing has its own button.
 */

import { api, ApiError } from "./api.js";

const FIELDS = [
  {
    field: "base_url",
    label: "服务地址",
    placeholder: "https://ollama.com/v1",
    help: "服务根地址，必须带上 API 版本段。默认值是 Ollama 云端地址，但本机无法验证过它，错了也很正常——用下面的“测试连接”确认。",
  },
  {
    field: "api_key",
    label: "API Key",
    placeholder: "留空表示不修改已保存的值",
    secret: true,
    help: "只保存在本机数据库里。接口从不回显它，页面也拿不到它。",
  },
  {
    field: "model",
    label: "模型 ID",
    placeholder: "gpt-oss:120b",
    help: "真正发给服务端的模型名。填错会得到 404 或 400，不会静默失败。",
  },
  {
    field: "provider_name",
    label: "显示名称",
    placeholder: "ollama-cloud",
    help: "只是界面上的标签，不影响请求内容。",
  },
];

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function createSettingsPanel({ onChanged }) {
  const dialog = document.getElementById("settings-dialog");
  const body = document.getElementById("settings-body");
  const status = document.getElementById("settings-status");
  const message = document.getElementById("settings-message");
  const openButton = document.getElementById("settings-button");

  const inputs = {};
  let busy = false;

  function setMessage(text, kind = "info") {
    message.textContent = text || "";
    message.className = `settings-message ${kind}`;
    message.hidden = !text;
  }

  function renderStatus(settings) {
    const effective = settings.effective || {};
    const stored = settings.stored || {};
    status.textContent = "";
    const rows = [
      ["当前状态", effective.configured ? "可用" : "未配置"],
      ["API Key", effective.has_api_key
        ? (stored.api_key ? "已保存（面板填的）" : "已保存（来自环境变量）")
        : "未设置"],
      ["服务地址来源", { settings: "本面板", env: "环境变量", default: "内置默认值（未经验证）" }[effective.sources ? effective.sources.base_url : ""] || "—"],
      ["模型来源", { settings: "本面板", env: "环境变量", default: "未设置" }[effective.sources ? effective.sources.model : ""] || "—"],
    ];
    for (const [label, value] of rows) {
      const row = el("div", "settings-row");
      row.appendChild(el("span", "settings-key", label));
      row.appendChild(el("span", "settings-value", value));
      status.appendChild(row);
    }
    if (settings.env_overrides && settings.env_overrides.length) {
      // The one thing that makes a working panel look broken: the reader saves a value, and
      // another one takes effect.  Say which and why.
      const names = settings.env_overrides
        .map((field) => (FIELDS.find((entry) => entry.field === field) || { label: field }).label)
        .join("、");
      status.appendChild(el(
        "p",
        "settings-warning",
        `环境变量正在覆盖：${names}。本面板保存的值已被写入，但不会生效，直到该环境变量被取消。`,
      ));
    }
  }

  function renderFields(settings) {
    body.textContent = "";
    for (const spec of FIELDS) {
      const row = el("div", "settings-field");
      const label = el("label", "settings-label", spec.label);
      label.htmlFor = `setting-${spec.field}`;
      const input = document.createElement("input");
      input.id = `setting-${spec.field}`;
      input.className = "settings-input";
      input.type = spec.secret ? "password" : "text";
      input.placeholder = spec.placeholder || "";
      input.autocomplete = "off";
      // A password field is populated from the *stored* view, which for a secret is a boolean,
      // so this stays empty on purpose.  Nothing readable is ever put here.
      if (!spec.secret && settings.stored && settings.stored[spec.field] !== undefined) {
        input.value = settings.stored[spec.field];
      }
      inputs[spec.field] = input;
      label.appendChild(input);
      row.appendChild(label);
      if (spec.help) row.appendChild(el("p", "settings-help", spec.help));
      body.appendChild(row);
    }
  }

  async function load() {
    try {
      const settings = await api.getTranslationSettings();
      renderStatus(settings);
      renderFields(settings);
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : String(error), "error");
    }
  }

  async function save() {
    if (busy) return;
    busy = true;
    try {
      const payload = {};
      for (const spec of FIELDS) {
        const value = inputs[spec.field].value;
        if (spec.secret) {
          // Blank means "leave the stored key alone".  Clearing is a separate, deliberate act,
          // so a reader editing only the model cannot wipe the key by accident.
          if (value) payload[spec.field] = value;
        } else {
          payload[spec.field] = value;
        }
      }
      const settings = await api.putTranslationSettings(payload);
      renderStatus({ ...settings, stored: settings.stored || {} });
      if (inputs.api_key) inputs.api_key.value = "";
      setMessage("已保存。不需要重启任何进程，下一个任务就会用它。", "ok");
      if (onChanged) await onChanged();
    } catch (error) {
      setMessage(error instanceof ApiError ? `${error.message}（${error.code}）` : String(error), "error");
      if (error instanceof ApiError && error.detail) renderStatus({ effective: {}, stored: {} });
    } finally {
      busy = false;
    }
  }

  async function test() {
    if (busy) return;
    busy = true;
    setMessage("正在向配置的服务地址发一次真实请求……", "info");
    try {
      const report = await api.testTranslationSettings();
      const where = `${report.endpoint}（模型 ${report.model}）`;
      if (report.ok) {
        setMessage(
          `连通：${where} 用时 ${report.latency_ms} ms，返回 ${JSON.stringify(report.sample)}。` +
          "这次检查没有写入任何翻译结果。",
          "ok",
        );
      } else {
        setMessage(
          `失败：${where}\n${report.error_code}：${report.error_message}` +
          (report.http_status ? `（HTTP ${report.http_status}）` : ""),
          "error",
        );
      }
    } catch (error) {
      setMessage(error instanceof ApiError ? `${error.message}（${error.code}）` : String(error), "error");
    } finally {
      busy = false;
    }
  }

  async function clearAll() {
    if (busy) return;
    busy = true;
    try {
      await api.clearTranslationSettings();
      for (const spec of FIELDS) inputs[spec.field].value = "";
      await load();
      setMessage("已清除面板保存的全部设置，回退到环境变量和内置默认值。", "ok");
      if (onChanged) await onChanged();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : String(error), "error");
    } finally {
      busy = false;
    }
  }

  document.getElementById("settings-save").addEventListener("click", save);
  document.getElementById("settings-test").addEventListener("click", test);
  document.getElementById("settings-clear").addEventListener("click", clearAll);
  document.getElementById("settings-close").addEventListener("click", () => dialog.close());
  openButton.addEventListener("click", async () => {
    setMessage("");
    dialog.showModal();
    await load();
  });

  return { open: () => openButton.click() };
}
