// SPDX-License-Identifier: Apache-2.0
/**
 * A ZIP archive read in the browser: an Office file is one. Bounded, because the file is an agent's:
 * a compressed entry claims one size and may inflate to another, so the limit is applied to what
 * actually comes out, not to what the archive declares.
 */

const END_OF_DIRECTORY = 0x06054b50;
const DIRECTORY_ENTRY = 0x02014b50;
const LOCAL_ENTRY = 0x04034b50;
const MAX_ENTRIES = 5_000;
const MAX_ENTRY_BYTES = 64 * 1024 * 1024;
const MAX_TOTAL_BYTES = 256 * 1024 * 1024;

type Entry = { method: number; compressed: number; offset: number };

export class OfficeFileError extends Error {}

export class Zip {
  private consumed = 0;

  constructor(
    private readonly view: DataView,
    private readonly entries: Map<string, Entry>,
  ) {}

  has(path: string): boolean {
    return this.entries.has(path);
  }

  async bytes(path: string): Promise<Uint8Array | null> {
    const entry = this.entries.get(path);
    if (!entry) return null;
    const { view } = this;
    if (view.getUint32(entry.offset, true) !== LOCAL_ENTRY) throw new OfficeFileError("damaged archive");
    const start = entry.offset + 30 + view.getUint16(entry.offset + 26, true) + view.getUint16(entry.offset + 28, true);
    if (start + entry.compressed > view.byteLength) throw new OfficeFileError("damaged archive");
    const raw = new Uint8Array(view.buffer, view.byteOffset + start, entry.compressed);
    if (entry.method === 0) return this.counted(raw);
    if (entry.method !== 8) throw new OfficeFileError("unsupported compression");
    return this.inflate(raw);
  }

  async text(path: string): Promise<string | null> {
    const data = await this.bytes(path);
    return data && new TextDecoder().decode(data);
  }

  private counted(data: Uint8Array): Uint8Array {
    this.consumed += data.byteLength;
    if (data.byteLength > MAX_ENTRY_BYTES || this.consumed > MAX_TOTAL_BYTES) throw new OfficeFileError("archive too large");
    return data;
  }

  private async inflate(raw: Uint8Array): Promise<Uint8Array> {
    const source = new ReadableStream<BufferSource>({
      start(controller) {
        controller.enqueue(raw as BufferSource);
        controller.close();
      },
    });
    const reader = source.pipeThrough(new DecompressionStream("deflate-raw")).getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_ENTRY_BYTES || this.consumed + size > MAX_TOTAL_BYTES) {
        await reader.cancel();
        throw new OfficeFileError("archive too large");
      }
      chunks.push(value);
    }
    const out = new Uint8Array(size);
    let at = 0;
    for (const chunk of chunks) {
      out.set(chunk, at);
      at += chunk.byteLength;
    }
    this.consumed += size;
    return out;
  }
}

export function openZip(buffer: ArrayBuffer): Zip {
  const view = new DataView(buffer);
  let end = -1;
  for (let at = view.byteLength - 22; at >= Math.max(0, view.byteLength - 65_557); at -= 1) {
    if (view.getUint32(at, true) === END_OF_DIRECTORY) {
      end = at;
      break;
    }
  }
  if (end < 0) throw new OfficeFileError("not an Office file");
  const count = view.getUint16(end + 10, true);
  let at = view.getUint32(end + 16, true);
  if (count > MAX_ENTRIES) throw new OfficeFileError("archive too large");
  const entries = new Map<string, Entry>();
  const decoder = new TextDecoder();
  for (let index = 0; index < count; index += 1) {
    if (at + 46 > view.byteLength || view.getUint32(at, true) !== DIRECTORY_ENTRY) throw new OfficeFileError("damaged archive");
    if (view.getUint16(at + 8, true) & 1) throw new OfficeFileError("this file is encrypted");
    const nameLength = view.getUint16(at + 28, true);
    const name = decoder.decode(new Uint8Array(buffer, at + 46, nameLength));
    entries.set(name, {
      method: view.getUint16(at + 10, true),
      compressed: view.getUint32(at + 20, true),
      offset: view.getUint32(at + 42, true),
    });
    at += 46 + nameLength + view.getUint16(at + 30, true) + view.getUint16(at + 32, true);
  }
  return new Zip(view, entries);
}
