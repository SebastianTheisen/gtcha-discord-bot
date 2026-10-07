// Dialoge (Rückfragen mit Knöpfen), Haptik und kurze Meldungen - von überall aufrufbar
import { signal } from "@preact/signals";
import type { ComponentChildren } from "preact";
import { Sheet } from "./components/Sheet";

interface Dialog {
  title: string;
  body: ComponentChildren;
  buttons: string[];
  resolve: (v: string | null) => void;
}

const dialog = signal<Dialog | null>(null);
export const toast = signal<string | null>(null);

// Rückfrage: löst mit dem Text des gedrückten Knopfs auf (null = daneben getippt)
export function ask(title: string, body: ComponentChildren, buttons: string[] = ["Nein", "Ja"]): Promise<string | null> {
  return new Promise((resolve) => {
    dialog.value?.resolve(null);
    dialog.value = { title, body, buttons, resolve };
  });
}

export function closeDialogs() {
  dialog.value?.resolve(null);
  dialog.value = null;
}

let toastTimer: number | undefined;
export function showToast(text: string) {
  toast.value = text;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (toast.value = null), 2600);
}

export function DialogHost() {
  const d = dialog.value;
  const t = toast.value;
  return (
    <>
      {d && (
        <Sheet onClose={() => { d.resolve(null); dialog.value = null; }}>
          <h3 style={{ margin: "0 0 8px" }}>{d.title}</h3>
          <div class="dialog-body">{d.body}</div>
          <div class="dialog-buttons">
            {d.buttons.map((label, i) => (
              <button class={`btn ${i === d.buttons.length - 1 ? "primary" : ""}`}
                onClick={() => { dialog.value = null; d.resolve(label); }}>{label}</button>
            ))}
          </div>
        </Sheet>
      )}
      {t && <div class="toast" role="status">{t}</div>}
    </>
  );
}

// Haptisches Feedback: iOS 18 vibriert beim Umschalten eines Schalters (input switch), sonst navigator.vibrate
let hapticBox: HTMLLabelElement | null = null;
export function haptic() {
  try {
    if (!hapticBox) {
      hapticBox = document.createElement("label");
      hapticBox.style.cssText = "position:fixed;left:-99px;width:1px;height:1px;overflow:hidden";
      hapticBox.innerHTML = '<input type="checkbox" switch>';
      document.body.appendChild(hapticBox);
    }
    hapticBox.click();
  } catch {
    /* egal */
  }
  navigator.vibrate?.(12);
}
