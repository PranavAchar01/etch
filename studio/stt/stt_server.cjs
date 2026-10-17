// Local speech-to-text for Etch live mode: NVIDIA Parakeet TDT 0.6B v3 (int8 ONNX) on sherpa-onnx, CPU, offline.
// Model and runtime are the ones hyperframes already cached in ~/.cache/hyperframes (nothing is downloaded).
// POST /stt with raw little-endian Float32 PCM at 16 kHz mono -> {"text": "...", "seconds": 0.4}
//   node studio/stt/stt_server.cjs        (listens on 127.0.0.1:3421; flux_server proxies /etch/stt to it)
const http = require("http");
const os = require("os");
const path = require("path");
const C = process.env.ETCH_STT_HOME || path.join(os.homedir(), ".cache/hyperframes");
const sherpa = require(path.join(C, "optional/sherpa-onnx-node@1.13.8-darwin-arm64/node_modules/sherpa-onnx-node"));
const M = path.join(C, "parakeet/parakeet-tdt-0.6b-v3-int8");
const t0 = Date.now();
const rec = new sherpa.OfflineRecognizer({
  featConfig: { sampleRate: 16000, featureDim: 80 },
  modelConfig: {
    transducer: { encoder: `${M}/encoder.int8.onnx`, decoder: `${M}/decoder.int8.onnx`, joiner: `${M}/joiner.int8.onnx` },
    tokens: `${M}/tokens.txt`, numThreads: 4, provider: "cpu", debug: 0, modelType: "nemo_transducer",
  },
  decodingMethod: "greedy_search",
});
console.log(`parakeet loaded in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/stt") { res.writeHead(404); return res.end(); }
  const chunks = [];
  let n = 0;
  req.on("data", (c) => { n += c.length; if (n > 16000 * 4 * 60) req.destroy(); else chunks.push(c); });
  req.on("end", () => {
    const t = Date.now();
    const buf = Buffer.concat(chunks);
    const samples = new Float32Array(buf.buffer, buf.byteOffset, Math.floor(buf.length / 4));
    const s = rec.createStream();
    s.acceptWaveform({ sampleRate: 16000, samples: Float32Array.from(samples) });
    rec.decode(s);
    const text = rec.getResult(s).text.trim();
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ text, seconds: (Date.now() - t) / 1000, audio_s: samples.length / 16000 }));
  });
}).listen(3421, "127.0.0.1");
