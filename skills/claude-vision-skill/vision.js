#!/usr/bin/env node
/**
 * 独立识图脚本 — 调用千问 VL 模型，按量付费。
 *
 * 用法:
 *   node vision.js <图片路径> [问题]
 *   node vision.js --url <图片链接> [问题]
 *
 * 配置（按优先级）:
 *   DASHSCOPE_API_KEY 环境变量 或 同目录 .env 文件（需 npm install dotenv）
 *   > 同目录 config.json 的 api_key 字段
 *
 * 模型列表: 同目录 models.txt（一行一个），额度用尽时自动按顺序切换，
 *           并记住上次成功的模型（.vision-model-state.json）。
 */

const fs = require("fs");
const path = require("path");
const https = require("https");
const http = require("http");

// 尝试加载 .env（先找当前目录，再找脚本所在目录）
try { require("dotenv").config(); } catch {}
try { require("dotenv").config({ path: path.resolve(__dirname, ".env") }); } catch {}

const BASE_URL = process.env.DASHSCOPE_BASE_URL || "https://dashscope.aliyuncs.com/compatible-mode/v1";
const MODEL = process.env.VISION_MODEL || "qwen3-vl-235b-a22b-thinking";
const MODELS_FILE = path.resolve(__dirname, "models.txt");
const STATE_FILE = path.resolve(__dirname, ".vision-model-state.json");
const CONFIG_FILE = path.resolve(__dirname, "config.json");

function loadApiKey() {
  if (process.env.DASHSCOPE_API_KEY) return process.env.DASHSCOPE_API_KEY;
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8")).api_key || "";
  } catch { return ""; }
}

const API_KEY = loadApiKey();

function loadModels() {
  const single = process.env.VISION_MODEL;
  if (single) return [single];
  const list = process.env.VISION_MODELS;
  if (list) return list.split(",").map((s) => s.trim()).filter(Boolean);
  if (fs.existsSync(MODELS_FILE)) {
    const fromFile = fs.readFileSync(MODELS_FILE, "utf8")
      .split(/\r?\n/).map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#"));
    if (fromFile.length) return fromFile;
  }
  return [MODEL];
}

function loadStartIndex(models) {
  try {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    if (Number.isInteger(state.index) && state.index >= 0 && state.index < models.length) return state.index;
  } catch {}
  return 0;
}

function saveStartIndex(index) {
  try { fs.writeFileSync(STATE_FILE, JSON.stringify({ index })); } catch {}
}

function shouldFailover(statusCode, code, message) {
  if (statusCode === 429 || statusCode === 403 || statusCode === 404) return true;
  if (statusCode !== 400) return false;
  const text = `${code || ""} ${message || ""}`.toLowerCase();
  return ["quota", "limit", "throttl", "rate", "balance", "arrear",
    "allocation", "free", "exhaust", "expired", "not exist", "notfound",
    "invalidmodel", "unsupported", "access denied", "permission", "disabled",
    "额度", "余额", "欠费", "耗尽", "到期", "未开通", "不存在", "不支持", "限流"]
    .some((k) => text.includes(k));
}

function parseArgs() {
  const argv = process.argv.slice(2);
  let imageSource = "", prompt = "", isUrl = false;

  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--url" && argv[i + 1]) {
      isUrl = true;
      imageSource = argv[++i];
    } else if (!imageSource && !argv[i].startsWith("--")) {
      imageSource = argv[i];
    } else if (imageSource && !argv[i].startsWith("--")) {
      prompt = prompt ? prompt + " " + argv[i] : argv[i];
    }
  }
  if (!prompt) prompt = "请详细描述这张图片的内容。";
  return { imageSource, prompt, isUrl };
}

function resolveImageUrl(source, isUrl) {
  if (isUrl) return source;
  const resolved = path.resolve(source);
  if (!fs.existsSync(resolved)) throw new Error(`文件不存在: ${resolved}`);
  const ext = path.extname(resolved).toLowerCase().replace(".", "");
  const mimeMap = { jpg: "jpeg", jpeg: "jpeg", png: "png", gif: "gif", webp: "webp", bmp: "bmp" };
  const data = fs.readFileSync(resolved);
  return `data:image/${mimeMap[ext] || "jpeg"};base64,${data.toString("base64")}`;
}

function request(payload) {
  const url = new URL(BASE_URL.replace(/\/?$/, "/") + "chat/completions");
  const body = JSON.stringify(payload);
  const transport = url.protocol === "https:" ? https : http;

  return new Promise((resolve, reject) => {
    const req = transport.request(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    }, (res) => {
      let data = "";
      res.on("data", (c) => data += c);
      res.on("end", () => {
        if (res.statusCode >= 400) {
          let code = "", message = data.slice(0, 300);
          try {
            const parsed = JSON.parse(data);
            code = parsed?.error?.code || parsed?.code || "";
            message = parsed?.error?.message || message;
          } catch {}
          const err = new Error(`API ${res.statusCode}${code ? ` [${code}]` : ""}: ${message}`);
          err.statusCode = res.statusCode;
          err.errorCode = code;
          err.errorMessage = message;
          return reject(err);
        }
        try {
          resolve(JSON.parse(data)?.choices?.[0]?.message?.content || data);
        } catch { resolve(data); }
      });
    });
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

async function main() {
  if (!API_KEY) {
    console.error("未找到 API Key：请在 config.json 中填写 api_key，或设置 DASHSCOPE_API_KEY 环境变量。");
    console.error("获取 Key: https://bailian.console.aliyun.com/");
    process.exit(1);
  }
  const { imageSource, prompt, isUrl } = parseArgs();
  if (!imageSource) {
    console.error("用法: node vision.js <图片路径> [问题]");
    console.error("      node vision.js --url <图片链接> [问题]");
    process.exit(1);
  }
  try {
    const imageUrl = resolveImageUrl(imageSource, isUrl);
    const models = loadModels();
    const startIndex = loadStartIndex(models);
    let lastErr = null;
    for (let i = startIndex; i < models.length; i++) {
      try {
        const result = await request({
          model: models[i],
          messages: [{ role: "user", content: [
            { type: "image_url", image_url: { url: imageUrl } },
            { type: "text", text: prompt },
          ]}],
          stream: false,
          max_tokens: 1024,
        });
        saveStartIndex(i);
        console.log(result);
        return;
      } catch (err) {
        lastErr = err;
        if (!shouldFailover(err.statusCode, err.errorCode, err.errorMessage)) throw err;
        console.error(`模型 ${models[i]} 暂不可用（${err.errorCode || "HTTP " + err.statusCode}），自动切换到下一个…`);
      }
    }
    console.error("识图失败: 所有视觉模型均不可用。", lastErr?.message || "");
    process.exit(1);
  } catch (err) {
    console.error("识图失败:", err.message);
    process.exit(1);
  }
}

main();
