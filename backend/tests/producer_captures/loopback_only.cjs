// Loaded before the real CLI. No vendor transport or inherited user credentials are needed.
const net = require("node:net");
const fs = require("node:fs");
const original = net.Socket.prototype.connect;

net.Socket.prototype.connect = function (...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  const options = typeof first === "object"
    ? first
    : { port: first, host: typeof args[1] === "string" ? args[1] : "localhost" };
  const host = options.host ?? options.hostname ?? "localhost";
  if (options.path || host !== "127.0.0.1") {
    if (process.env.NATIVE_CAPTURE_NETWORK_LOG) {
      fs.appendFileSync(process.env.NATIVE_CAPTURE_NETWORK_LOG, JSON.stringify({ host }) + "\n");
    }
    throw new Error("Native CLI capture permits only its literal loopback model fixture");
  }
  return original.apply(this, args);
};
