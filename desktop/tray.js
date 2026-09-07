const { Tray, Menu, nativeImage } = require("electron");
const zlib = require("zlib");

const LISTENING_COLOR = { r: 61, g: 191, b: 106 };
const IDLE_COLOR = { r: 142, g: 142, b: 147 };
const SIZE = 32;
const SCALE = 2;

let tray = null;
let listening = false;
let crcTable = null;

function getCrcTable() {
  if (crcTable) {
    return crcTable;
  }
  crcTable = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    crcTable[index] = value >>> 0;
  }
  return crcTable;
}

function crc32(buffer) {
  let crc = 0xffffffff;
  const table = getCrcTable();
  for (const byte of buffer) {
    crc = table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])));
  return Buffer.concat([length, typeBuffer, data, crc]);
}

function encodePng(width, height, pixels) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  const rows = [];
  for (let y = 0; y < height; y += 1) {
    const start = y * width * 4;
    rows.push(Buffer.from([0]), pixels.subarray(start, start + width * 4));
  }
  const compressed = zlib.deflateSync(Buffer.concat(rows), { level: 9 });
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", compressed),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function dotPng({ r, g, b }) {
  const pixels = Buffer.alloc(SIZE * SIZE * 4);
  const center = (SIZE - 1) / 2;
  const radius = SIZE * 0.34;
  const outline = radius + SIZE * 0.08;
  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const dx = x - center;
      const dy = y - center;
      const distance = Math.sqrt(dx * dx + dy * dy);
      const fill = Math.max(0, Math.min(1, radius + 0.65 - distance));
      const ring = Math.max(0, Math.min(1, outline + 0.65 - distance)) - fill;
      const index = (y * SIZE + x) * 4;
      const shade = 32;
      pixels[index] = Math.round(r * fill + shade * ring);
      pixels[index + 1] = Math.round(g * fill + shade * ring);
      pixels[index + 2] = Math.round(b * fill + shade * ring);
      pixels[index + 3] = Math.round(255 * Math.max(fill, ring * 0.9));
    }
  }
  return encodePng(SIZE, SIZE, pixels);
}

function iconFor(isListening) {
  const image = nativeImage.createFromBuffer(dotPng(isListening ? LISTENING_COLOR : IDLE_COLOR), {
    width: SIZE,
    height: SIZE,
    scaleFactor: SCALE,
  });
  image.setTemplateImage(false);
  return image;
}

function buildMenu({ onShow, onToggle, onEndSession, onNewSession, onQuit }) {
  return Menu.buildFromTemplate([
    {
      label: listening ? "Listening" : "Idle",
      enabled: false,
    },
    { type: "separator" },
    {
      label: listening ? "Pause" : "Listen",
      accelerator: "CommandOrControl+Shift+L",
      click: onToggle,
    },
    {
      label: "End session",
      accelerator: "CommandOrControl+Shift+E",
      click: onEndSession,
    },
    {
      label: "New session",
      accelerator: "CommandOrControl+Shift+N",
      click: onNewSession,
    },
    {
      label: "Open window",
      click: onShow,
    },
    { type: "separator" },
    {
      label: "Quit",
      click: onQuit,
    },
  ]);
}

function refreshMenu(handlers) {
  if (!tray) {
    return;
  }
  tray.setImage(iconFor(listening));
  tray.setToolTip(listening ? "AI Interview · listening" : "AI Interview · idle");
  tray.setContextMenu(buildMenu(handlers));
}

function createTray(handlers) {
  if (tray) {
    return tray;
  }
  tray = new Tray(iconFor(false));
  tray.on("click", () => {
    handlers.onShow();
  });
  refreshMenu(handlers);
  return tray;
}

function setTrayListening(isListening, handlers) {
  listening = Boolean(isListening);
  refreshMenu(handlers);
}

function destroyTray() {
  tray?.destroy();
  tray = null;
}

module.exports = {
  createTray,
  setTrayListening,
  destroyTray,
};
