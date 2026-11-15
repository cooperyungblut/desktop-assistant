// Pictures waiting to go with the next question: pasted (Ctrl+V), dropped on the
// chat panel, or picked with the picture button. They show as thumbnails above the
// question box until sent (typed or spoken) or removed.
//
// Each one is shrunk in the browser before it goes anywhere: the model looks at a
// picture of about 1,600 px at most anyway, and a phone photo would otherwise be a
// 5 MB WebSocket message and a slower vision pass.

export const MAX_PICTURES = 4; // matches secretary/images.py
const MAX_SIDE = 1568;

/** A File/Blob image -> a JPEG data URL no larger than MAX_SIDE on its long side. */
export async function shrink(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; // transparent PNGs (screenshots of apps) on white, not black
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bitmap, 0, 0, w, h);
  bitmap.close?.();
  // JPEG at high quality keeps small text readable and is a fraction of a PNG.
  return canvas.toDataURL("image/jpeg", 0.92);
}

/** The image files in a paste or drop (DataTransfer). */
export function imageFiles(transfer) {
  if (!transfer) return [];
  const files = [...(transfer.files ?? [])];
  if (!files.length) {
    for (const item of transfer.items ?? []) {
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
  }
  return files.filter((f) => f.type.startsWith("image/"));
}

export class PictureTray {
  constructor(container, { onChange = () => {}, onError = () => {} } = {}) {
    this.container = container;
    this.onChange = onChange;
    this.onError = onError;
    this.items = []; // data URLs
  }

  get count() { return this.items.length; }

  async add(files) {
    for (const file of files) {
      if (this.items.length >= MAX_PICTURES) {
        this.onError(`Up to ${MAX_PICTURES} pictures at a time.`);
        break;
      }
      try {
        this.items.push(await shrink(file));
      } catch (err) {
        console.warn(err);
        this.onError("That picture couldn't be opened.");
      }
    }
    this.render();
  }

  remove(i) {
    this.items.splice(i, 1);
    this.render();
  }

  /** Put pictures back (a spoken question that wasn't heard). */
  restore(urls) {
    this.items = [...urls, ...this.items].slice(0, MAX_PICTURES);
    this.render();
  }

  /** Hand over the pictures for sending and clear the tray. */
  take() {
    const out = this.items;
    this.items = [];
    this.render();
    return out;
  }

  render() {
    this.container.replaceChildren(...this.items.map((url, i) => {
      const chip = document.createElement("span");
      chip.className = "picture-chip";
      const img = document.createElement("img");
      img.src = url;
      img.alt = `Picture ${i + 1}`;
      const x = document.createElement("button");
      x.type = "button";
      x.className = "secondary";
      x.textContent = "×";
      x.title = "Remove this picture";
      x.setAttribute("aria-label", `Remove picture ${i + 1}`);
      x.addEventListener("click", () => this.remove(i));
      chip.append(img, x);
      return chip;
    }));
    this.container.hidden = this.items.length === 0;
    this.onChange(this.items.length);
  }
}

/** Thumbnails for a sent message's chat bubble. */
export function thumbnails(urls) {
  const row = document.createElement("span");
  row.className = "picture-row";
  for (const url of urls) {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "Picture you sent";
    row.append(img);
  }
  return row;
}
