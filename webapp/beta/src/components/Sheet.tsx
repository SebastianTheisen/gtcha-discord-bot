import type { ComponentChildren } from "preact";
import { useEffect } from "preact/hooks";

// Bottom-Sheet: von unten einfahrendes Fenster (schließt per Tipp daneben oder Escape)
export function Sheet({ onClose, children }: { onClose: () => void; children: ComponentChildren }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", key);
    document.body.style.overflow = "hidden";
    return () => {
      removeEventListener("keydown", key);
      document.body.style.overflow = "";
    };
  }, [onClose]);
  return (
    <>
      <div class="sheet-bg" onClick={onClose} />
      <div class="sheet" role="dialog" aria-modal="true">
        <div class="grab" />
        {children}
      </div>
    </>
  );
}
